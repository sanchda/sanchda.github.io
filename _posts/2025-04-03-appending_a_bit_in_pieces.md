---
layout: post
date: 2025-03-31
title: "Appending a Bit, in Pieces"
tags: systems-programming, linux
---
For some reason or another, I keep getting involved in what I can only describe as "deconstructive engineering."
I'll be working on a system and suddenly a primitive operation becomes a point of significant friction within the greater whole.
It becomes as though the entire weight of a system (be it latency, complexity, or perhaps reliance on one specific unfortunate way of doing a thing) rests on a single interface.

Usually, this is fine.
But sometimes, you're rewarded with the truly obscene:  a critical performance sensitivity to what is "obviously" a primal, non-decomposable system operation.
Such as appending to a file.

I'm going to meander through the setup for a while.
If that's not your style, skip to the "write between the lines" section.

# Building with blocks to block the builds
Many runtimes provide high-level abstractions over classes of common operations.
Files are among the most common, so when a developer performs operations on such an object the various overheads and trade-offs can be quite opaque.
Let's be explicit about the required system interactions.
Concerning ourselves exclusively with the given example, consider:

```
    int fd = open(filename.c_str(), O_WRONLY | O_CREAT, 0644);
...
    lseek(fd, 0, SEEK_END);
    ssize_t written = write(fd, buffer, sz_buffer);
```

Now, the `lseek()` in the middle is a little problematic if you have multiple entities performing the same operation simultaneously on the same file (but not the same file _descriptor_).
Thankfully, there's a way to make _all_ `write` operations append to the end of the file, which moves the `lseek()` component effectively into the kernel.

```
    int fd = open(filename.c_str(), O_WRONLY | O_APPEND | O_CREAT, 0644);
...
    ssize_t written = write(fd, buffer, sz_buffer);
```
Not only is this better for the reason above, it also reduces the number of syscalls we require per `write()`.
But what are the scaling properties?

## How to be Write
At a high level, it's completely logical to look at `write()` and conclude a successful return status indicates data has been written to the underlying storage substrate.
Consequently, this would mean that the weight of any IO operation is embodied within the `write()`.
Unfortunately, logic fails us--this is not the case.

Briefly, the control flow looks like this.
1. Userland process calls `write()`
2. syscall interrupt triggers, ultimately calling the `__do_sys_write()` (or whatever) handler in the kernel
3. The kernel calls `vfs_write()`
4. Some more indirection happens (`do_sync_write()`), but eventually the file is expanded, its inode is marked dirty, the associated pages are marked dirty, and control is returned
5. At some later point in time, for any number of reasons, writeback is triggered; if actual IO occurs, the IO scheduler handles the request

This is actually a somewhat superficial/incorrect sketch, but the fundamental observation is that even "synchronous" write operations can be asynchronous to a large degree.

## Example: Centralized Logging
This example was inspired by a few systems I've worked on--I don't believe the specific details will detract too much from my goal, but insofar as they _do_, you may want to know that I've never seen this precise system before, all put together as I have it here.

Consider a distributed system, comprised of some highly specialized components, and some other parts that provide generic utilities.
One such service is a central logging authority (CLA):
1. Individual components send logs to CLA over TCP by serializing messages into a context-laden binary format
2. CLA deserializes the message and appends the resulting string to a file
3. CLA calls `fsync()`
4. CLA responds to the request, allowing the client to continue

All of this infrastructure is coordinated at the level of the host, so a single host has a single log.
This makes ingestion and rotation particularly simple.

These properties were not enough to keep that design a steady-state--as the application grew, a few things happened:
1. The number of requests served on a single machine increased
2. The number of log lines per request increased
3. Other aspects of the system improved, so the amount of load capable of being served in aggregate on a single machine also increased

What emerged was a seemingly fixed cap to system request rate--at a certain point, no matter how application behavior was tuned, the rate maxed out.
It was known that logging was somehow related, since disabling logging had what was considered an outsized effect on request throughput.
Throughput was a key measure related to profitability (less throughput -> more computers to serve customer needs), so this became an issue.

### Rejecting the Axioms
You might look at this system and wonder how it was possible for a real, profitable, sophisticated piece of technology maintained by good, thoughtful people to possess a global serialization point.
Largely, it was because these properties did not come together by accident.
This strategy weakly (see [this](https://lwn.net/Articles/752063/) discussion on `fsync()` in postgres for some details) ensures individual components cannot traverse to a new state until any meaningful context on their current state has been persisted.

There are a number of systems for which this is a principled, non-negotiable aspect of their operation, and the horizontal scaling of those systems becomes a key part of their maintenance.
This, however, was not one of them.
We worked together to arrive at the following conclusions:
* Applications logs are a best-effort attempt at storing the state of the application
* It is not necessary to guarantee logs are written before allowing components to continue
* Application logs aren't generally that useful for resolving machine-level crashes

Accordingly, the `fsync()` was removed and the dynamics of message passing were updated to allow a fire-and-forget strategy.
This yielded quite a bit of scaling headroom.
But, unfortunately, not forever.

### Leaving Holes to the Lagomorphs
It can be desirable to solve problems like this as quickly as possible and move on, but I get my guidance from strange places, and I wanted to give this the benefit of deeper consideration.
Writing this, the thing I have on my mind is the [CFL condition](https://en.wikipedia.org/wiki/Courant%E2%80%93Friedrichs%E2%80%93Lewy_condition).
Conceptually, this condition is saying something like this:  if you're driving at night, and your stopping distance is longer than what your headlights can illuminate, you're in trouble.

As we seek to provide more abstract, farther-reaching value to our respective organizations, we become headlights.
You have to choose where you shine your light.
You do you, this is one of the things I've opted to brighten.

### A Better Logger
There are a number of off-the-shelf systems similar to what I sketched above, but they tend to fall into one of two archetypes.
Both models are compatible with the idea of doing dispatch off-thread.
I think this is a great solution, I think most people should pursue it instead of what I'm writing about here.

If you're satisfied with that much, I don't think you should bother yourself with the rest of what I have here.
Thank you--until next time.

##### Client emits one syscall per message
* pro: message is immediately written
* con: impossible to experience less than a context switch worth of overhead for short messages

##### Client buffers messages before flushing
* pro: less than one syscall per message
* con: messages are not immediately written

#### Verdict
In my discussion above, I made the point that we had agreed it would be OK to elide an `fsync`, given that it isn't vital for messages to make their way to storage during a system crash.
Given this point, one might read the summaries above and conclude we're similarly fine missing immediacy for writing logs.
Not so.

When systems crash, it is very rarely the result of one of the applications you run.
On the other hand, when your application crashes, it is very often because of one of the applications you run.
On that basis, immediacy is a useful property for logs targeting user behavior, not so useful for system behavior.
Plus you have `dmesg` and the whole kit that came with your OS, so don't get too greedy.

So what do we do?
Let's reach for both.
As usual, solution is preceded by some boring trivia.

### All the pages I dislike and none of the devices I admire
Note that the following discussion contains many simplifications for the sake of narrative simplicity.
The general feel of things is a little more important than the details--which eats at me a bit, and hopefully it bothers you too.
I don't think anything here is utterly wrong, though.

If you care to look, there's some variation in the fossil record when it comes to memory models.
You may have experienced x86 real mode, or even logical addressing.
There yet remain extant species with *banked memory* even today, like some Microchip PIC lines.

Compare and contrast with the things I take for granted--paged memory.
(The following discussion assumes _shared_ mappings for reasons which will become evident either eventually or possibly never)

Think about it this way--`mmap()` wasn't standardized into POSIX until POSIX.1b-1993.
I believe, and I may be wrong on this, that `mmap()` first appeared in 1988 on SunOS 4.0, but not 4.3BSD (1986), although the former was heavily influenced by the VM work within the latter.

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

Now, it doesn't take a lot of creativity to imagine that compared to number crunching, this process of preempting executing to fiddle with page table entries can't be cheap--and it isn't.
Indeed, if you're very intentionally mutating a large range of pages, clearly this can be somewhat laborious, compared to just directly beaming down a sequence of writes.

#### The chase is better than the cache
I made the mistake of bringing up number crunching, so I think we ought to contemplate very carefully the dynamics of memory access.
Wires don't scale with Moore's law, and so memory IO has not kept pace with arithmetic over the last 30-40 years.
In order to deal with this, contemporary platforms implement hardware caches in order to keep pools of memory closer to the _thinking_ part of the machine.

I don't want to get too deep into the weeds, so let's assume we're on an architecture where our shared memory page is PIPT--physically indexed, physically tagged.
This means that any access by any process requires no aliasing and the kernel requires no explicit flush operations.

An extremely simplified and borderline (or possibly _actually_) incorrect summary for *access* operations:
1. CPU translates the process-level virtual memory to a physical address using TLB (... or walks page table)
2. CPU checks L1-L3 in order, if it fails it gets pulled from RAM (this is guaranteed, since the page access would hit an error if no page fault had occurred)
3. Cache controller ensures coherence between cores by copying pages around if needed

And for modifications:
1. Again with the TLB
2. Again with the hierarchy check
3. Before mutation, check with the other cores
4. S (shared) pages become invalidated and other cores drop their copies
5. Page transitions to M (modified)
6. Page transitions to L1, marked dirty
7. Depending on a number of dynamic factors, the write-back eventually happens

If you're a programmer, the essentially thing to keep in mind is that the CPU tries really, really hard to juggle the page between the different cores and different layers of cache, shuffling (close to) the minimum number of copies around.
This is really important, and it's actually the crux of why many lock-free data structures end up being more expensive than their fully-locked brethren.
It's not hard to imagine a world where your beautiful lock-free structure is spread out over a large number of pages, necessitating endless and pointless CAS after CAS after CAS.

To put it another way, a file-backed mapping is virtually indistinguishable from a heap mapping when it comes to cross-task communication.
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

I've [prepared](github.com/sanchda/systems_experiments/fastlog), if you'd like to follow along.

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


#### Cursing
This use of an atomic integer as a means of atomically taking a contiguous position is

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

#### Cursing, except you have to expand when you do it
Let's track what the metadata does when a cursor hits the end of the file.
Suppose we have a setup where the chunk size is one page, and we allocated just two at the beginning.
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

In order to get there to here,
1. Notice that your cursor is too big
2. Try to lock the file, checking cursor size and retrying until you have it
3. Compute the new file size, based on your needs
4. If the current file size is smaller, call `ftruncate()` on the file to expand it and release the lock

### Duo Ex Nihilo
Before we can do _any_ of that, though, we have to create the files.
We want to do so in a way that doesn't presume any one process will be empowered to take its time and set everything up while everyone else watches.
We need to handle the case where two prospective creators butt heads.

### A Ring of Chunks
