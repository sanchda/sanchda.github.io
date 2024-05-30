---
layout: post
date: 2024-05-28
title: "argv and memfd_secret"
tags: systems-programming, linux, security
---

In an earlier post I wrote a few things about `argv`, the array of strings that every application receives when it starts.
That article demonstrated how modifying `argv` values also changes the results of `ps` and `top`.
In other words, the sequence of operations which leads to tools such as `ps` and `top` acquiring process arguments involves reading the live memory of a running application.

I'd like to play with this idea a little bit more.
In particular, while the last post did show that modifying the `argv` array does indeed change the output of `/proc/<pid>/cmdline`, it did so at the expense of modifying the running process in a non-compliant way.
Let's do something similar, but in a way that is non-compliant _differently_.
What we're going to do is keep a secret from the kernel itself.

### The procedure
What we want to do is make `argv` secret.
Let's review some properties of `argv` and maps in general.

1. `argv` and the `envp` (environment variables) are serialized in a contiguous fashion
2. `mmap()` with `MAP_FIXED` can be used to replace a region of memory at a given address
3. mappings, their permissions, and their page-time behavior are determined at page granularity

What we will do is identify the range of pages that `argv` occupies, copy them to the side, and then replace those ranges with a secret mapping that cannot be read by anyone.
Then, we'll confirm that the process can read its own argv in the normal way, but that various typical system implements cannot.


## memfd
If the concept of a _memfd_ is new to you, consider the following sequence of operations.
1. Choose a random path, which happens to be on a tmpfs filesystem.
2. `open()` that path, getting a file descriptor.
3. Call `ftruncate()` and `mmap()` on the resulting file descriptor.

This is a technique for creating a region of memory which is backed by page table.
Sometimes, you want an anonymous region of memory, but also a file descriptor.
This tempfile workflow has some issues, though.
For instance:  guarantee that a given path is indeed backed by tmpfs.
But also:  guarantee that all users will give you access to the virtual file system in the first place.

`memfd_create()` solves some of these problems by allowing one to create a file descriptor which essentially skips to step 3.
Moreover, one can turn a file descriptor into a virtual file system path via `/proc/self/fd/<num>`.


## memfd_secret()
`memfd_secret()` is a variant of this operation.
Except the region you map via `mmap()` can only be accessed by processes with the file descriptor.
Conceptually, even though these pages are backed by page table, they can't even be read by the kernel.


### Creating a secret
The first thing to note is that the `memfd_secret()` syscall does not have a glibc wrapper, so we have to call it directly.
`memfd_secret()` takes a single argument, a combination of flags which define optional behavior.
Right now, it only supports `FD_CLOEXEC`.
We'll ignore it for the purpose of demonstration.

```
#include <sys/syscall.h>
...
int fd = syscall(SYS_memfd_secret, 0);
```


### Copying the secret
If we're going to mark `argv` pages as secret, then we need to figure out exactly which pages to use.
For simplicity, let's assume we're going to do this from `main()`, so we already have `argv` and `argc`.
To some degree, the exact in-memory layout of entities like `argv` and `envp` is under-defined by things like POSIX, the System V ABI, and the C standard(s).
However, recall that these structures are populated by the kernel during process instantiation.
A [quick review](https://github.com/torvalds/linux/blob/master/fs/exec.c#L535) might give us some confidence that `argv` is indeed contiguous.

The range sweep will span the first byte of `argv[0]` to the last byte of `argv[argc-1]`, including the containing pages.
```
#define PAGE_SZ 4096 // Not PAGE_SIZE because we don't want to conflict

// My `ALIGN` macro here is over-adapted to covering cases where I'm doing integral offsets and also pointers.
// You may not want to use this.
#define ALIGN(x, a) (typeof(x)) (((uintptr_t)(x) + ((uintptr_t)(a) - 1)) & ~((uintptr_t)(a) - 1))
#define PAGE_ALIGN(x) ALIGN(x, PAGE_SZ) // Round "up" (down?) to the nearest page
#define PAGE_TRUNC(x) (PAGE_ALIGN(x) - PAGE_SZ) // Gets the top of the containing page

...

unsigned char *first_page = PAGE_TRUNC(argv[0]);
unsigned char *last_page = PAGE_TRUNC(argv[argc - 1] + strlen(argv[argc - 1]) + 1); // +1 for the zero byte.  Not that it matters!
size_t num_pages = (last_page - first_page) / PAGE_SZ;
```

In order to use the memfd we created, it has to be sized properly.
```
ftruncate(fd, num_pages * PAGE_SZ);
```

At this point, a few operations remain.
1. Copy the data from `argv` into the memfd.
2. Replace the range of `argv` pages with those from the memfd.

There's a slight catch-22.
We can't copy data into the memfd without mapping it.
In particular, we can't use `write(fd, ...` (this was my first instinct, but it doesn't appear to work).
On the other hand, if we map _over_ the target range, we won't have access to the data we need to copy.

The typical move would be to `malloc()` or `mmap()` a buffer, then copy into it.
Let's do that, but with a twist.
```
unsigned char *copy = mmap(NULL, num_pages * PAGE_SZ, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
memcpy(copy, first_page, num_pages * PAGE_SZ);
unsigned char *secret = mmap(first_page, num_pages * PAGE_SZ, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_FIXED, fd, 0);
munmap(copy, num_pages * PAGE_SIZE);
close(fd);
```

Did you spot the trick?
We mapped `fd` anonymously, copied the data, then mapped `fd` _again_ over the target range with `MAP_FIXED`.
This allowed us to perform only one copy.
The downside is we needed to emit an additional `mmap()` (although it's possible `malloc()` would have done approximately the same thing).


### Checking our work
All right, let's see what happened.
First, let's confirm that we didn't do anything to mess up our own ability to read `argv`.
```
for (int i = 0; i < argc; ++i) {
    printf("argv[%d] = %s\n", i, argv[i]);
}
```

Then, let's read our own `/proc/self/cmdline`.
Conceptually, if reads to these pages are being suppressed somehow, we should get anything from an error to a 0 byte string.
It may also be the case that things are smart enough to tell that the calling process instantiated the memfd in the first place.
Let's see!

It's completely orthogonal to what we're doing here, but recall that procfs isn't mappable.
It requires `read()` type operations.
That means operations like `splice` and `sendfile` won't work.
Here's a simple loop to read and shunt into stdio.
```
unsigned char buffer[PAGE_SZ];
ssize_t read_sz;
int cfd = open("/proc/self/cmdline", O_RDONLY);

while (1) {
  read_sz = read(cfd, buffer, PAGE_SZ);
  if (read_sz == -1 && errno == EINTR) continue; // Standard retry condition
  else if (read_sz <= 0) break;

  // cmdline is 0-delimited, so make it pretty
  for (int i = 0; i < PAGE_SZ; ++i)
    if (!buffer[i])
      buffer[i] = ' ';

  ssize_t write_sz = 0;
  while (write_sz < read_sz) {
    ssize_t written = write(1, buffer + write_sz, read_sz - write_sz);
    if (written == -1 && errno == EINTR) continue;
    else if (written <= 0) break;
    write_sz += written;
  }
}
```

I'm not really going to break this down, but if this is new to you just note the retry for EINTR (signals, usually).

## The eating
We saw the pudding.
What's next?
I built a small test application called `secret`.
Here's the output without protecting argv
```
$ ./secret hello world
argv[0] = ./secret
argv[1] = hello
argv[2] = world
Proc cmdline:
./secret hello world
```

Here's what things look like after protection has been applied.
```
argv[0] = ./secret
argv[1] = hello
argv[2] = world
Proc cmdline:

```

In particular, `cmdline` is empty.
This is noteworthy because the data is there.
We can read it just fine.
But now, it cannot be read externally, even by the same process via procfs.

Here's a little bit more color.
```
$ ps -p <pid> -o args
COMMAND
./secret a b c d ef

# After applying the secret
$ ps -p <pid> -o args
COMMAND
[secret]
```

As before, we see that the output of `ps` has the value of `argv` stripped out.
But what's up with the square brackets?
Well, turns out that the "comm" field in `ps` can be pulled from a few sources.
How can we tell?
Because the backup source is limited in length:
```
$ ps -p 11619 -o args
COMMAND
./abcdefghijklmnopqrstuvwxyz

# After applying the secret
$ ps -p 11619 -o args
COMMAND
[abcdefghijklmno]
```

See?
Not even the name of the process is being harvested from the "right" place.


## Closing thoughts
I'd like to point out how bizarre this is.
Conceptually, you might imagine that operations such as "hide my arguments from procfs" would be tied to individual processes.
We didn't do such a thing.
The modification we _did_ do involved merely changing the type of mapping particular pages in the process address space had.
