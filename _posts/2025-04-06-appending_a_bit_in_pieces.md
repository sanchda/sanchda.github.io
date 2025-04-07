---
layout: post
date: 2025-04-06
title: "Appending a Bit, in Pieces"
tags: systems-programming, linux
---
This article will go over a few things--IO, CPU caches, shared memory, atomics, juggling overheads, and the tradeoffs that emerge in systems design.
We're going to meander for a bit; go over some documentation, ponder the interfaces, look at an example, and then finally sketch out an absurd abomination of a library and compare it against a few obvious strategies.

# Your Building Blocks are a Convenient Lie
Runtimes generally provide high-level abstractions over classes of common operations.
Files are common, so when a developer performs operations on such objects, the various overheads and trade-offs can be quite opaque.
Let's be explicit about the required system interactions:

```
    int fd = open(filename.c_str(), O_WRONLY | O_CREAT, 0644);
...
    lseek(fd, 0, SEEK_END);
    ssize_t written = write(fd, buffer, sz_buffer);
```

Conceptually, what we do when we append to a file is:
1. Open the file
2. Make sure operations are staged to the end of the file
3. Write the data to the file

The `lseek()` in the middle is problematic if you have multiple entities performing the same operation concurrently.
It would be possible to have two processes `lseek()` to the current end cursor, and then both write to the same location.
Thankfully, there's a way to make the `lseek()` part atomic and implicit--improving the correctness of the implementation, while also improving the syscall overhead.

```
    int fd = open(filename.c_str(), O_WRONLY | O_APPEND | O_CREAT, 0644);
...
    ssize_t written = write(fd, buffer, sz_buffer);
```

Despite how many moving parts are involved in writing to storage, it's interesting that the user-facing components are non-decomposable.
There are ways (like `writev()` and even `io_uring`) to stage multiple write operations in a single operation, but you can't exactly break `open()` or even `write()` into pieces.

An incredibly unnatural question to ask, when presented with this observation, is: how can I encumber myself with even more pointless trivia and code-level complexity for the mere hope of one day extracting a tiny sliver of _different_ (not necessarily improved, just **different**) functionality?
Glad you asked!
Let's break it down.

## Being Wrong about Write
It's logical to look at `write()` and conclude--the documentation states explicitly that the return specifies "the number of bytes _written_--that it writes to a storage device.
Plus, it's in the name!
For better or worse, this isn't the case--even when `O_DIRECT` is specified, in contemporary Linux the operation merely, at best, enqueues an operation with the IO scheduler.

Briefly, the control flow _might_ look like this:
1. Userland process calls `write()`
2. syscall interrupt triggers, ultimately calling the `__do_sys_write()` (or whatever) handler in the kernel
3. The kernel calls `vfs_write()`
4. Some more indirection happens (`do_sync_write()`, page table), but eventually the file is expanded, its inode is marked dirty, the associated pages are marked dirty, and control is returned
5. At some later point in time, for any number of reasons, writeback is triggered; if actual IO occurs, the IO scheduler handles the request

If you're not in a `write()`-heavy application, the dominating overhead of `write()` may very well be the syscall itself.

Of course, the lack of a serialization point between 4 and 5 is a lie.
If it wasn't, then you could just keep enqueuing writes endlessly until something--memory, probably--goes kaput.
Any system in which requests can be indefinitely enqueued faster than they can be evicted is unstable.

When it comes to IO, the kernel has a number of mechanisms by which to create backpressure.
For example, there's dirty page throttling.
There are device-level request queues.
cgroups has the `blkio` controller.

This makes it quite difficult to build a mental model for how to anticipate IO overheads.
At some scales of operation, the dominant overhead is the syscall.
At others, the overhead is the IO itself.
In yet more, the issue is _other processes_ gobbling up IO, and you have to wait for the bus along with the rest of them.
Are you on a low latency (network) bus?
Do you have dynamic IOP provisioning?

Most of these points won't be resolved with an example, but let's conveniently change the subject by talking through one.

## Example: Centralized Logging
Consider a distributed system, comprised of some highly specialized components, and some other parts that provide generic utilities.
One such service is a central logging authority (CLA):
1. Individual components send logs to CLA over TCP by serializing messages into a context-laden binary format
2. CLA deserializes the message and appends the resulting string to a file
3. CLA calls `fsync()`
4. CLA responds to the request, allowing the client to continue

All of this infrastructure is coordinated at the level of the host, so a single host has a single log.
There's no particular need or reason for this, except it's how the system grew up and it's become an operational assumption which is hard for the team to relax.
At least, it makes ingestion and rotation particularly simple without the need for intermediate processing or additional IO.

These properties were not enough to keep that design a steady-state--as the application grew, a few things happened:
1. The number of requests served on a single machine increased
2. The number of log lines per request increased
3. Other aspects of the system improved, so the amount of load capable of being served in aggregate on a single machine also increased

What emerged was a seemingly fixed cap to system request rate--at a certain point, no matter how application behavior was tuned, the service rate maxed out.
It was known that logging was somehow related, since disabling logging had what was considered an outsized effect on request throughput.
Throughput was a key measure related to profitability (less throughput -> more computers to serve customer needs), so this became an issue.

The service maintainers had showed that picking up a log file from a day's worth of operation and writing it back to disk took seconds.
Capacity planning had included bulk IO measurements, which showed provisioned write IOPs were more than sufficient for projected need.
So, what could be going wrong?

Fate went wrong.
The numbers looked different when the file was written line-by-line with `fsync()` in-between.
Bulk IO was measured, but it wasn't what mattered.

### Rejecting the Axioms
You might look at this system and wonder how it was possible for a real, profitable, sophisticated piece of technology maintained by good, thoughtful people to possess a global serialization point.
Largely, it was because these properties did not come together by accident.
This strategy weakly (see [this](https://lwn.net/Articles/752063/) discussion on `fsync()` in postgres for some details) ensures individual components cannot traverse to a new state until any meaningful context on their current state has been persisted.

In other words, the given design tries (but in the strictest sense fails) to guarantee individual messages are written to storage, even if the very next line of code manages to unplug the power cord.

There are a number of systems for which this is a principled, non-negotiable aspect of their operation, and the horizontal scaling of those systems becomes a key part of their maintenance.
This, however, was not one of them.
We worked together to arrive at the following conclusions:
* Applications logs are a best-effort attempt at storing the state of the application
* It is not necessary to guarantee logs are written before allowing components to continue
* Application logs aren't generally that useful for resolving machine-level crashes

Accordingly, the `fsync()` was removed and the dynamics of message passing were updated to allow a fire-and-forget strategy.
This yielded quite a bit of scaling headroom.
But, unfortunately, not forever.

## A Better Logger
There are a number of off-the-shelf systems similar to what I sketched above, but they tend to fall into one of two archetypes.

##### Client emits one syscall per message
* pro: message is immediately written
* con: impossible to experience less than a context switch worth of overhead for short messages

##### Client buffers messages before flushing
* pro: less than one syscall per message
* con: messages are not immediately written

##### Both and Other
I'm missing a category of techniques here, which involve performing the dispatch off-thread.
I think these are often great solutions, but off-thread adventures aren't always viable or desirable, since they also involve asynchronicity.
The category is acknowledged here and ignored.

#### Verdict
Earlier I made the point that the team agreed it would be OK to elide an `fsync`, given that it isn't vital for messages to make their way to storage during a system crash.
Given this, one might read the summaries above and conclude it's fine to lose immediacy for sending logs out-of-process.
Not so.

When systems crash, it is very rarely the result of one of the applications you run.
On the other hand, when your application crashes, it is very often because of one of the applications you run.
On that basis, immediacy is a useful property for logs targeting user behavior, not so useful for system behavior.
Plus you have `dmesg` and the whole kit that came with your OS, so don't get too greedy.

So what do we do?
Let's reach for both.
As usual, solution is preceded by some boring trivia.

### All the pages I dislike and none of the devices I admire
Note that the following discussion contains quite a few simplifications, and I'll confess I don't believe I understand the dynamics of all the systems fully.
As is the prevailing convention of the time, let's assume **vibe** trumps all.
I don't think anything here is utterly wrong, though.

If you care to look, there's some variation in the fossil record when it comes to memory models.
You may have experienced x86 real mode, or even logical addressing.
There yet remain extant species with *banked memory* even today, like some Microchip PIC lines.

Compare and contrast with the things I take for granted--paged memory.
(The following discussion assumes _shared_ mappings for reasons which will become evident either eventually or possibly never)

Think about it this way--`mmap()` wasn't standardized into POSIX until POSIX.1b-1993.
As far as I've been able to tell, it didn't even appear until 1988 on SunOS 4.0.

This isn't exactly accidental.
`mmap()` requires a paged memory system, really.
It relies on memory protection, demand-paging--a virtual memory system that supports page-based management.

So, start from the bottom.
You've got a disk device, which is exposed as a block device to the "user" (whatever that is).
This is an interface exposing fixed-sized blocks of data, and admitting random access.

What do you have in the other hand?
Paged memory.
This is an interface exposing fixed-sized blocks of data, and admitting random access.

What you can do is this:
1. In the kernel, reserve a range of virtual addresses--don't immediately allocate
2. Set up the page table entries (with the MMU) to mark these addresses as "not present," so any access triggers a page fault
3. When a fault occurs, the CPU traps to the kernel, engaging a page resolution mechanism--copy the page(s) and resume as though nothing happened

On the write side:
1. When first setting up the mapping, mark the range as *write only*
2. When the first write occurs, the CPU traps into the kernel, which updates the page entry as writable, marks the pages as dirty, and resumes execution (write succeeds)
3. Later, dirty pages are copied back down to the device and the cycle continues

Note that step 2 here is how the kernel imposes write-time backpressure on paged IO--execution isn't resumed in the application until whatever prevailing condition has expired.

Compared with number crunching, this process of preempting executing to fiddle with page table entries can't be cheap--and it isn't.
Indeed, if you're very intentionally mutating a large range of pages, this can be somewhat laborious, compared to just directly beaming down a sequence of writes.

#### The chase is better than the cache
When I built this system in the past, I used a combination of semaphores and pthreads primitives.
People forget, but pthreads objects can be shared across process boundaries (super useful!).
For this cut, I wanted to forego the use of these and stick to coordination over shared memory.
I think this means we should sketch the dynamics here at a very high level, at least.

Wires don't scale with Moore's law, and so memory IO has not kept pace with arithmetic over the last 30-40 years.
In order to deal with this, contemporary platforms implement hardware caches for keeping pools of memory closer to the _thinking_ part of the machine.

I don't want to get too deep into the weeds, so let's assume we're on an architecture where our shared memory page is PIPT--physically indexed, physically tagged.
This means that any access by any process requires no aliasing and the kernel requires no explicit flush operations.
(the term PIPT may have no relevance to you--that's okay, we'll talk about caches in more depth in a later post)

An extremely simplified and borderline (or possibly _actually_) incorrect summary for *access* operations:
1. CPU translates the process-level virtual memory to a physical address using TLB (... or walks page table)
2. CPU checks L1-L3 in order, if it fails its cache line gets pulled from RAM (this is guaranteed, since the page access would hit an error if no page fault had occurred)
3. Cache controller ensures coherence between cores by copying cache lines around if needed

I'm using the term "cache line" here without any introduction.
It's the quantum of data access between/within CPU caches and main memory.
As you might guess, these are aligned to physical addresses (64 bytes for x86_64).

Anyway, for modifications:
1. Again with the TLB
2. Again with the hierarchy check
3. Before mutation, check with the other cores
4. S (shared) cache lines become invalidated and other cores drop their copies
5. Cache line transitions to M (modified)
6. Cache line transitions to L1, marked dirty
7. Depending on a number of dynamic factors, the write-back eventually happens

For the purpose of this discussion, the essential thing to keep in mind is that the CPU tries to juggle the cache lines between the different cores and different layers of cache, shuffling (close to) the minimum number of copies around.
This is important, and it's actually the crux of why many lock-free data structures end up being more expensive than their fully-locked brethren.

It's not hard to imagine a world where your beautiful lock-free structure is spread out over memory, which necessitates endless and pointless CAS after CAS after CAS.

To put it another way, a file-backed mapping is virtually (ha!) indistinguishable from a heap mapping when it comes to cross-task communication.
And that's the insight we need to start us off.

# Write Between the Lines
What we want to do is write-append, and we're willing to take on some up-front cost to do it, but:
* Writes should be instantaneous (from the perspective of page table)
* Multiple processes should be able to append safely to the same file
* The syscall/message ratio should be tunable
* The implementation should make use of the narrative garbage I littered the preceding sections with
* The actual data written should be generic--we want a regular file

Here's the idea:
1. Maintain two files, a metadata file and a data file--both will be `MAP_SHARED`
2. The metadata file consists of a serialized struct, which will contain information about the current write cursor, file size, semaphores, etc
3. When a task wishes to write, it will atomically advance the cursor by the specified size
4. If a pending write exceeds the size of the file, it will ftruncate by a certain amount (chunk size)
5. Provide a `trim` operation to remove the excess file size; it should be idempotent

### Knowing when to fold
If you're totally satisfied with that much, then this is where we part ways.
The rest of this article is a discussion on how you actually achieve the desired atomicity properties given the interfaces we have.
This includes not just coordination at the level of data, but also manipulating dual files.

I've [prepared](https://github.com/sanchda/systems_experiments/tree/main/fastlog) some code, if you'd like to follow along.
Although this isn't necessary for comprehension.

### Metadata
First thing's first, let's look at the coordination struct.
Note that this is all in C11, since I had written it to incorporate into an existing codebase with its own build system.

```
typedef struct {
    uint32_t version;
    _Atomic bool is_ready;
    _Atomic bool is_locked;
    _Atomic bool is_panicked;
    _Atomic uint64_t file_size;
    _Atomic uint64_t cursor;
    uint32_t page_size;
    uint32_t chunk_size;
} log_metadata_t;
```

| Field       | Purpose                       |
|-------------|-------------------------------|
| version     | I'm allowed to change my mind |
| is_ready    | Atomic, indicating metadata initialization is done |
| is_locked   | Atomic, indicating metadata is in a sensitive intermediate state |
| is_panicked | Atomic, Bail out, can't use this |
| file_size   | Synced to size of file |
| cursor      | Points to the location where the next write operation should occur |
| page_size   | Chunk size is a multiple of this, plus we need to know it for mmap |
| chunk_size  | Defines how much to grow the file when needed |

The really important thing to look at here is the `cursor` and the `chunk_size`, since these define a lot of the downstream dynamics.

#### Cursing
This use of an integer as a means of atomically taking a contiguous position is something of a pattern in shared-memory coordination.
That doesn't exactly make it well-known outside of a niche field.
Let's break it down.

Initially, suppose the log file looks like this:
```
+---------------------- Shared Log File ------------------------+
|                                                               |
| ...written data... | <-------- unwritten space -------->      |
|                    ^                                          |
|                  cursor                                       |
+---------------------------------------------------------------+
                    ^                   ^
                    |                   |
               Process A wants      Process B wants
               to write 100B        to write 200B
```

Process A has a message of 100B to write, so it advances the cursor.
Atomicity guarantees that concurrency is mediated through the CPU's infrastructure.
A is able to deduce the beginning location of the cursor from the return value and the size.
```
+---------------------- Shared Log File ------------------------+
|                                                               |
| ...written data... | Process A's section | <-- unwritten -->  |
|                    ^                     ^                    |
|                old cursor             new cursor              |
+---------------------------------------------------------------+
                    |                     |
                    +----------+----------+
                               |
            Atomic increment by A moves cursor from 500 to 600
```

Process B concurrently does the same, receiving its own region.
```
+---------------------- Shared Log File ------------------------+
|                                                               |
| ...written data... | Process A | Process B | <-- unwritten -> |
|                    ^           ^           ^                  |
|                  500          600         800                 |
+---------------------------------------------------------------+
                                |           |
                                +-----------+
                                      |
                             B's turn for atomic increment
```

As long as A and B stay within their regions, this allows multiple processes to safely write to the same file simultaneously.

#### fembiggen()
One of the key distinctions between this setup and a shared-memory ringbuffer is the fact that the target region can and must resize under normal operations.
This makes it a little trickier, especially the error conditions, but let's see how it might work.

We'll track what the metadata does when a cursor hits the end of the file.
Suppose we have a setup where the chunk size is one page, and we allocated just one at the beginning.

```
+-------------------+     +-------------------+
| Metadata          |     | Data File         |
|-------------------|     |-------------------|
| version: 1        |     | +--------------+  |
| is_ready: true    |     | | Chunk 1      |  |
| is_locked: false  |     | |              |  |
| is_panicked: false|     | +--------------+  |
| file_size: 4096   | --> | +- - - - - - - +  |
| cursor: 3000      |     | | Chunk 2         |
| page_size: 4096   |     |  (if I had one)|  |
| chunk_size: 4096  |     | + - - - - - - -+  |
+-------------------+     +-------------------+
                            ^
                            | file_size = 4096
```

At this point, the cursor is safely within the first chunk, but not forever.
A client wants to write 3000 more bytes.
That no longer fits!
At the end of an expansion process, what we want is:
```
+-------------------+     +-------------------------+
| Metadata          |     | Data File (Expanded)   |
|-------------------|     |------------------------|
| version: 1        |     | +--------------+       |
| is_ready: true    |     | |.Chunk 1......|       |
| is_locked: false  |     | |....(full)....|       |
| is_panicked: false|     | +--------------+       |
| file_size: 8192   | --> | +--------------+       |
| cursor: 5000      |     | | Chunk 2      |       |
| page_size: 4096   |     | |              |       |
| chunk_size: 4096  |     | +--------------+       |
+-------------------+     +-------------------------+
                                     ^
                                     | new file_size = 8192
```

In order to get from there to here,
1. Notice that your cursor is too big
2. Try to lock the file, checking cursor size and retrying until you have it
3. Compute the new file size, based on your needs
4. If the current file size is smaller, call `ftruncate()` on the file to expand it and release the lock

You might admit a small race somewhere between 1 and 3, which allows other processes to coordinate on a desired maximum size.
The idea is to use a speculative size field and just ratchet it to the biggest chunk boundary needed by the given cursor.
I didn't use this pattern, but I thought it was worth pointing out.

### Duo Ex Nihilo
Before we can do _any_ of that, though, we have to create the files.
We want to do so in a way that doesn't presume any one process will be empowered to take its time and set everything up while everyone else watches.
We need to handle the case where two prospective creators butt heads.

To make matters worse, we have _two_ files to coordinate.
Whatever will we do?

1. Try to `open(... O_CREAT)` the metadata file
2. If we succeeded, we are the unique process who just created a file--yay
3. If we failed, then either the file cannot be created or it already exists--try to open with backoff for a bit
4. The creator has to `ftruncate()` up to the size of the metadata, while the waiter sits on an `fstat()` loop until the size of the file changes
5. The creator is now free to perform a similar operation on the data file
6. The waiter `mmap()`s the metadata file and sits on `is_initialized` until it changes
7. When the creator is all done, it flips the `is_initialized` bit and everyone can proceed

Simple enough, but there are a few subtly critical conditions which may not be obvious.

1. The waiter can't map either file until it's been `ftruncated()`, since it'll have no size associated to it--existence has to be a two-part check
2. Fencing access to the data file behind the initialization of the metadata file makes it easier to structure an "all clear" signal in one place

### A Ring of Chunks
One of the many problems with linear memory addressing is that you can't blindly assume a given allocation can be expanded in-place.
After all, it could be bumping up against one of its neighbors.
For the same reason, in our case we can't just keep mapping the whole file, doing `mremap()` on a `MAP_FIXED` address along the way.

Plus, even if the first several pages get paged-out, and thus cease contributing to RSS, there are a few dynamics to consider:
1. Not everyone will have swap enabled
2. It's awkward keeping an unbounded number of resources (chunks) with open-ended lifetimes; they should be managed

For this library I decided to manage a ringbuffer of active chunks for a given process.
There are a few advantages:
1. Ensures each process only maintains a maximum buffer float (which is configurable)
2. Allows the library some opportunity to apply backpressure apart from the IO subsystem itself
3. Ringbuffers are fairly clean and easy to manage

#### Metadata Hierarchy
Here's a quick summary of how the different metadata elements refer to each other:

```
+---------------------+
|   log_metadata_t    |  (global state, shared between processes)
+---------------------+
| version             |
| is_ready            |
| is_locked           |
| is_panicked         |
| file_size           |
| cursor              |
| page_size           |
| chunk_size          |
+---------------------+
         ^
         |
+---------------------+
|    log_handle_t     |  (local state, shared between threads in a process)
+---------------------+
| metadata_fd         |
| data_fd             |
| metadata            | -> log_metadata_t* (from metadata_fd)
| chunks              | -> chunk_buffer_t*
+---------------------+
         |
         v
+---------------------+
|   chunk_buffer_t    | (ringbuffer implementation)
+---------------------+
| buffer              | -> buffer from `data_fd`
| capacity            |
| head                |
| tail                |
| head_locked         |
+---------------------+
```

#### Adding the First Chunk
```
 buffer: +---+----+----+----+----+
         | C |NULL|NULL|NULL|NULL|
         +---+----+----+----+----+
           ^
           |
         head
C:
  +------------------+
  | chunk_info_t     |
  +------------------+
  | start_offset     | <- aligned to chunk_size
  | size             | <- chunk_size
  | mapping          | <- mmap'd region
  | ref_count = 1    |
  +------------------+
```

#### Full Ringbuffer
```
 buffer: +---+---+---+---+---+
         | C1| C2| C3| C4| C5|
         +---+---+---+---+---+
           ^               ^
           |               |
         tail            head

When attempting to add a new chunk:
1. clean_chunks() is called first
2. Only chunks with ref_count=0 can be removed
3. If buffer is still full, returns an error
```

#### Cleanup
```
Before cleaning:
 buffer: +---+---+---+---+---+
         | C1| C2| C3| C4| C5|
         +---+---+---+---+---+
           ^               ^
           |               |
         tail            head

 ref_count: [0, 0, 1, 2, 1]

After cleaning:
 buffer: +---+---+---+---+---+
         |NULL|NULL| C3| C4| C5|
         +---+---+---+---+---+
                   ^       ^
                   |       |
                 tail    head

Process:
1. Check if tail chunk has ref_count=0
2. If yes, unmap and free the chunk
3. Advance tail pointer
4. Repeat until finding a chunk with ref_count > 0
```

#### A Minor Optimization
This doesn't really matter, but I'm really sensitive to the fact that log size isn't clamped by the interface.
I think it would be quite cumbersome to assume the caller either has to re-submit a buffer if it was too big, or just get their stuff silently truncated.
Accordingly, my implementation handles cross-chunk writes in two ways.

1. If something fits inside of a single chunk, great
2. If something crosses a single chunk boundary, then expand if needed, but otherwise write normally
3. Big writes

A big write will span at least one full chunk, meaning that the `mmap()` operation will serve exactly and only one single write.
If we're going to do _that_, why not just `pwrite()` and then avoid mapping the chunks in the first place?

In pictures:
```
file structure:
+----------+----------+----------+----------+
| chunk 0  | chunk 1  | chunk 2  | chunk 3  |
+----------+----------+----------+----------+
0        4096       8192      12288      16384

case 1: write fits within single chunk
+----------+----------+
| chunk 0  | chunk 1  |
+----------+----------+
     |-----|
    write (2kb)

case 2: write crosses chunk boundary
+----------+----------+
| chunk 0  | chunk 1  |
+----------+----------+
       |---------|
      write (3kb)

case 3: write spans multiple chunks (direct pwrite)
+----------+----------+----------+
| chunk 0  | chunk 1  | chunk 2  |
+----------+----------+----------+
     |-------------------|
         write (8kb)
```

Putting it all together:
```
mmlog_checkout():
1. atomic_fetch_add(&metadata->cursor, size)
              |
              v
2. Check if end > file_size
              |
              v
3. data_file_expand() if needed
              |
              v
4. Return cursor position

mmlog_insert() checkout and write flow:
   cursor = mmlog_checkout()
           |
           v
      crossings > 1?
      /         \
     Yes        No
    /             \
pwrite()    write_to_chunk()
            /       \
  First chunk     Second chunk
```

## Results
Let's compare this against a few other ways of appending to a file.

| Category | Description |
|----------|-------------|
| mmlog | this library |
| write + O_APPEND | write() with open(..., O_APPEND |
| writev + O_APPEND | same, but writev() instead of `write() |
| FILE | POSIX FILE streams |
| Direct I/O | open(..., O_DIRECT) to bypass page cache |
| Linux AIO | libaio |

Timings are done within a VM, on a machine (my laptop!) equipped with a fast local SSD.
If I were to brush this up and make it a more accessible library, I'd probably reveal benchmarks on some standard cloud configurations.
In my experience, `mmlog` shows a much more extreme win on a high-latency substrate, like EBS.

### Fixed-size small logs
This is approximately the target case from the example.
As you can see, `mmlog` leads the pack, although just by a small multiplicative factor.
Was it worth the effort?

> Running benchmarks with 4 processes, 100000 operations per process, 128 bytes per operation

| Category | Total Time (ms) | Time per Call (μs) | Throughput (GB/s) |
|----------|----------------|-------------------|------------------|
| mmlog | 101 | 0.25 | 3.777 |
| O_APPEND with write() | 388 | 0.97 | 0.983 |
| writev() with O_APPEND | 375 | 0.94 | 1.017 |
| FILE streams (fwrite) | 383 | 0.96 | 0.996 |
| Direct I/O (O_DIRECT) | 3841 | 9.60 | 0.099 |
| Linux AIO | 484 | 1.21 | 0.788 |


### One-byte writes
Here's a completely degenerative example.

> Running benchmarks with 4 processes, 100000 operations per process, 1 bytes per operation

| Category | Total Time (ms) | Time per Call (μs) | Throughput (GB/s) |
|----------|----------------|-------------------|------------------|
| mmlog | 16 | 0.04 | 0.186 |
| O_APPEND with write() | 379 | 0.95 | 0.008 |
| writev() with O_APPEND | 369 | 0.92 | 0.008 |
| FILE streams (fwrite) | 368 | 0.92 | 0.008 |
| Direct I/O (O_DIRECT) | 3886 | 9.71 | 0.001 |
| Linux AIO | 482 | 1.21 | 0.006 |

A quick note here.
`O_DIRECT` dispenses with some of the intermediate abstractions available to other write modes.
Accordingly, when IO is dispatched, `O_DIRECT` ensures the request at least hits the storage controller (albeit maybe not actual storage).
This is great when you're doing large writes, but for small writes like this, clearly latency is a dominating factor.

### Almost a Chunk
As we get closer to the chunk size, some problems start to emerge.
I think this is actually a sign that I have a bug in the implementation, but for the purpose of discussion let's take this as a loss.

> Running benchmarks with 4 processes, 10000 operations per process, 4095 bytes per operation

| Category | Total Time (ms) | Time per Call (μs) | Throughput (GB/s) |
|----------|----------------|-------------------|------------------|
| mmlog | 302 | 7.55 | 4.041 |
| O_APPEND with write() | 114 | 2.85 | 10.705 |
| writev() with O_APPEND | 162 | 4.05 | 7.533 |
| FILE streams (fwrite) | 118 | 2.95 | 10.342 |
| Direct I/O (O_DIRECT) | 397 | 9.93 | 3.074 |
| Linux AIO | 265 | 6.62 | 4.605 |

### That's Enough
A complete harness would also check what happens when the underlying IO system (either the scheduler, the bus, or the storage controller) hits a saturation point and dirty-page marking started to apply backpressure.
I'd normally deduce this threshold by using the `tracefs` system to instrument block latency.
Maybe another time!

## Final Thoughts
All right, so there you have it.
Please do be aware that I put this whole thing together as a sketch--the implementation is far from water-tight.
I think the performance can be improved quite a bit as well.

One of the weaknesses of shared-memory libraries is the fact that they can easily enter indeterminate intermediate states if a collaborating process crashes or is paused.
I know of two strategies around this (they amount to the same thing--there should be no unrecoverable intermediate states), but that's a story for a different night.

### Source Code
* [mmlog](https://github.com/sanchda/systems_experiments/tree/main/fastlog)
* [benchmark](https://github.com/sanchda/systems_experiments/tree/main/write_append)

### Credits
I'm grateful to anyone who reads this, but especially those who help me fix my mistakes or clear up my shortcomings.

Shout out to [Tanel Poder](https://www.linkedin.com/in/tanelpoder/) for realizing that I had written _page_ several times when I should have written _cache line_.
