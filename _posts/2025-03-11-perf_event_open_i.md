---
layout: post
date: 2025-03-11
title: "perf_event_open"
tags: systems-programming, linux, observability
---

We're going to go through a nontrivial, albeit pointless, example of `perf_event_open()`.
Now, `perf_event_open()` isn't exactly something we use every day, so this discussion might come across as totally pointless (because it is, unless you're in need of reference).
So here we go.

# perf_event_open()
This bad boy can hold _so many interfaces_.
But it lacks one itself.
Sad!
Let's fix that--here's the first thing you'll need on your journey.

```
#include <unistd.h>
#include <sys/syscall.h>
#include <linux/perf_event.h>

int
perf_event_open(struct perf_event_attr *attr,
                pid_t pid,
                int cpu,
                int group_fd,
                unsigned long flags) {
    return syscall(__NR_perf_event_open, attr, pid, cpu, group_fd, flags);
}
```

OK, so let's do something simple.
Maybe tracking process name changes?
Useless, but at least it'd let us see something unique.
First--an experiment:

```
int setup_perf_event() {
    struct perf_event_attr attr = {
        .type = PERF_TYPE_SOFTWARE,
        .config = PERF_COUNT_SW_DUMMY,
        .size = sizeof(struct perf_event_attr),
        .comm = 1,
    };

    int fd = perf_event_open(&attr, 0, -1, -1, 0);
    if (fd == -1) {
        perror("perf_event_open");
        exit(1);
    }
    return fd;
}
```

Here's a mistake I see very, _very_ good programmers make.
They march along, writing unit tests for their core implementation.
They mock system interfaces to see how the application responds to failure.
But they don't check the state of the system!

Let's check the system.
```
$ strace ./foo
...
perf_event_open({type=PERF_TYPE_SOFTWARE, size=PERF_ATTR_SIZE_VER7, config=PERF_COUNT_SW_DUMMY, sample_period=0, sample_type=0, read_format=0, comm=1, precise_ip=0 /* arbitrary skid */, ...}, 0, -1, -1, 0) = -1 EACCES (Permission denied)
```

Correct me if I'm wrong, but `EACCESS` is usually not a good thing.

## Paranoid about Perf
Oftentimes, usage of the `perf_event_open()` syscall is heavily constrained by the `perf_event_paranoid` setting.
The degree to which this matters depends on your configuration and system, but let's tiptoe through some historical discussion.

`perf_event_open()` is framed as a general-purpose frontend for all kinds of system observability data.
The interface should be widely available to processes who want to know more about themselves, and the sysadmin can opt into allowing them to know more about _other processes_ as well.
Broadly speaking, this is thematically aligned with the way we structure other interfaces and offers a spectrum of insight/power tradeoffs to the system maintainer.

Unfortunately, then the CVEs happened.
Lots of CVEs.
It was a bad time.

Seeing these CVEs, and realizing that the goal was at odds with the reality, a certain multinational corporation tried to assert a few "basic" security practices in the Linux kernel.
First, add a new level to `perf_event_paranoid` (the system-wide tunable for governing the baseline security model for `perf_event_open()`); whereas the "old" max was the value *2* (can only look at yourself), the new max would be *3* (can't use it without other escalation).
Second, make *3* the _default_ level.

For better or for worse, this attempt was thwarted--`perf_event_open()` is for all.
Hooray (but possibly also boo, hiss).

But, most organizations do not run "Linux."
They run distributions of Linux which are shaped and maintained by benevolent stewards.
They, in fact, run _patched_ kernels.

Debian took these rejected proposals as kernel patches.
And so, all downstream distros did so.
And so, many end-users and organizations did so.

#### Sidenote on Permissions
By the way, `perf_event_paranoid` is a pretty sad way of managing access across your infrastructure if you're running a container-based system.
In particular, this is a system-wide setting with no natural container-level override.
Before kernel 5.8 (Aug 2020, first rolled into Ubuntu 20.10 *Groovy Gorilla* in October of that year), the best one could do was use `CAP_SYS_ADMIN`.
This is an incredible amount of permission to grant!

That said, in 5.8, we gained the `CAP_PERFMON` capability for granular controller over this interface.
Much better!

## Fixing the Error
This type of error is extremely typical and I wanted to point it out at the start, since the category of an error (`EACCESS`) doesn't fully explain its cause.
`EACCESS` implies some resource exists, but cannot be interacted with for some reason.
The natural solution is to fix the thing most obviously related to _access_.

```
# cat /proc/sys/kernel/perf_event_paranoid
2
```

So, knowing what we know, this _should_ be a normal and acceptable value for the setting.
But I wanted to draw attention to something.
After the discussion above, and from reading around in the `perf_event_open()` manpage regarding `EACCESS`, you may be inspired to pursue this fix:

```
# echo 1 > /proc/sys/kernel/perf_event_paranoid
...
$ strace ./main
...
perf_event_open({type=PERF_TYPE_SOFTWARE, size=PERF_ATTR_SIZE_VER7, config=PERF_COUNT_SW_DUMMY, sample_period=0, sample_type=0, read_format=0, comm=1, precise_ip=0 /* arbitrary skid */, ...}, 0, -1, -1, 0) = 3
```

This certainly addresses the symptom, but the nature of the fix should be unsatisfying.
`perf_event_paranoid == 2` is supposed to be the _default_ configuration, which yields some token amount of useful information about a process.
Unfortunately, the requirements around `perf_event_paranoid()` are a little bit opaque, and it turns out this misunderstanding is not uncommon.

Let's roll back the `perf_event_paranoid` change and focus on another strategy.
We'll add one more setting to the config:

```
          .exclude_kernel = 1,
```

And it works.

When crafting an interface, you are juggling competing concerns.
Certainly you'd like the default configuration to also be the surest one to work in any context.
But you also want the rest of the configuration to be in semantic harmony.
Or you may wish to target the most typical/useful case.

Either way, you can't assume that a default configuration is the one that's going to work for you.

## Reading Events
Typically, an introduction to `perf_event_open()` will set up a counting type event, read the current count from the file descriptor, and leave the rest to one's imagination.
To some extent I guess this is fine, but this isn't a simple interface, and these exercises leave far too much unsaid.
Instead, I want to talk about the ringbuffer-mediated event interface.

Incidentally, observe that we specified a DUMMY type configuration:
```
        .type = PERF_TYPE_SOFTWARE,
        .config = PERF_COUNT_SW_DUMMY,
        .comm = 1,
```
What this means is, we'll be getting `COMM` events, but no sample type events.
That slightly simplifies the demonstration for now, but we'll make this more interesting in the sequel.

Anyway, our strategy here is to set up a shared-memory mediated event transmission system with the kernel.
What the kernel will do is serialize events to a hunk of memory which can be accessed by userspace, and we'll manipulate the metadata on that shared memory in order to tell the kernel we've processed an event.
This is nice for a few reasons.
Conspicuously, because once we're set, it allows us to read event data from the kernel without any context or mode switches.
It's also a generic system, shared by eBPF as part of its userspace communication interface.

### Overview
1. `mmap()` a ringbuffer twice
2. Manipulate the head/tail entries, deserialize the event
3. Use the event

### Mapping Twice
Every ringbuffer consists of a single metadata page, followed by a power of two data pages.
Let's check it out.  Here's a small ringbuffer to start out.

```
      unsigned char *buf = mmap(NULL, 4096 + 4096*2*2, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
```

There's just one problem, though.
Since this is a ringbuffer, it's not uncommon for events to wrap around the edges.
It isn't terribly _hard_ to deal with this, but it's fiddly and it's nice to not have to worry about all the fiddly things.
My colleague [nsavoire](https://github.com/nsavoire) introduced me to an utterly _wonderful_ solution to this problem, which takes advantage of how page cache can be used to multiplex a mapping.
The basic idea is to map the ringbuffer _twice_, side-by-side, so reads going off of the end spill into the next.

Something like this:
```
    // Size parameters
    static const size_t page_size = 4096;
    size_t rb_data = 2 * 2 * page_size;
    size_t rb_size = page_size + rb_data;

    // Actual mappings (please check for MAP_FAILED)
    unsigned char *base_addr = mmap(NULL, rb_size + rb_data, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    unsigned char *second_map = mmap(base_addr + rb_data, rb_size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_SHARED, fd, 0);
    unsigned char *first_map = mmap(base_addr, rb_size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_SHARED, fd, 0);

    // Return
    return base_addr;
```

The homework assignment is to create two back-to-back mappings of the data pages.
The problem is that the `perf_event_open()` file descriptor does not permit offset-based mappings--it's either all or nothing.
Moreover, you need to pre-allocate a contiguous range and fill in it order to have any guarantee that you'll be able to organize things side-by-side (dynamic loaders use this trick too).

Again, in words:
1. Compute the total range to be mapped, which is two data ranges and one metadata page--`page_size + 2 *rb_data = rb_size + rb_data`
2. Create a `PROT_NONE` mapping spanning this range
3. Map the _second_ ringbuffer first; the intuition here is that we have to map over the whole thing, so we'll _map over_ the metadata page with the first region
4. Map the _first_ ringbuffer second, starting from the beginning

Later on, if we ever tear down this region, record the size and the base address for future arguments to `munmap()`.

#### Perplexing Pedagogical Pugilism
So, in the leading discussion I took the liberty of an educational aid called "lying and also being wrong."
The examples I gave above "work" to precisely the extent they are tested, but they do not produce events in the ringbuffer.
If you've taken mathematics or physics especially (but probably also anything else--apparently we can mostly only teach through repeated deception), you're used to this.

Here's a rundown on the details I left out before, which I will include moving forward.

##### CPU Affinity
`perf_event_open()` allows the caller to define on which CPU cores to enable the instrumentation.
The special value `-1` is _supposed_ to allow the instrumentation to work on all cores.
In many configurations, it works exactly how it says on the tin and there's no need to do anything complicated about it.
On the other hand, there are configurations for which this specification will silently and mysteriously fail.
For instance, it seems that the ringbuffer implementation is handled on a per-core basis, and so when data is exfiltrated in this way, each core must be instrumented separately.

To fix this, one must:
1. Call `perf_event_open()` on each (schedulable) CPU core
2. Open a ringbuffer on _each_ returned file descriptor
3. Check every such ringbuffer for events

Savvy readers may think to use the first returned file descriptor as a `group_fd`, but this doesn't appear to work.

Here's an example of what that first step looks like:
```
typedef struct PerfEventConfig {
    int fds[1024];
    void *buffers[1024];
    size_t n;
} PerfEventConfig;

...

PerfEventConfig *config = malloc(sizeof(PerfEventConfig));
int num_cpus = get_nprocs();

for (int i = 0; i < num_cpus; i++) {
    int fd = perf_event_open(&attr, 0, i, -1, 0);
    if (fd == -1) {
        perror("perf_event_open");
        return config;
    }
    config->fds[i] = fd;
    config->buffers[i] = fd_to_buf(fd); // calls mmap, as before
    config->n = i;
}
```

`PerfEventConfig` doesn't _have_ to be a static array--I just took the simple way out here.
For your application, season to taste; this is just a convenient way to iterate through state.
Also note that `get_nprocs()` is a Linuxism for getting the current number of hardware cores available to the OS.
It may not describe the CPU mask for the current process and I _guess_ it'll imply some degree of fragility in the face of hotplug CPUs

Once you have the events, you have to iterate through them.
Here's a somewhat sloppy `poll()` loop.
Helpfully, the `perf_event_open()` file descriptors get marked readable when there is data in the ringbuffer.

```
struct perf_event_header *get_next_event(void **bufs, int *fds, size_t n) {
    struct pollfd pollfds[1024];
    for (size_t i = 0; i < n; i++) { // n is
        pollfds[i].fd = fds[i];
        pollfds[i].events = POLLIN;
    }

    int ret = poll(pollfds, n, -1);
```

The `perf_event_header` part comes from the `perf_event_open()` manpage:
```
struct perf_event_header {
    __u32   type;
    __u16   misc;
    __u16   size;
};
```

The idiomatic way of dealing with this is to interpret the first few bytes of every item strictly as a `perf_event_header`, and then after we've deduced the type we can do something different.

##### Water for my Marks (~Serial~Cereal for my Events)
Check out the following `strace` output, which shows `poll()` (called as above) hanging indefinitely, despite being preceded by a COMM change:
```
prctl(PR_SET_NAME, "poop")              = 0
poll([{fd=3, events=POLLIN}, ..., {fd=9, events=POLLIN}], 7, -1
```
What's up with this?
Why are we hanging?

Since the type of this configuration is `PERF_COUNT_SW_DUMMY`, it means that there's no underlying sampling event.
Mechanistically, the underlying kernel machinery for `perf_event_open()` works on the basis of counter overflows.
Once some threshold has been met (specified e.g., but not exclusively, by `sample_event`), an overflow occurs and data is flushed to the buffer.
For a configuration, such as ours, where the sampling event never occurs (intentionally!), it also means there's no precipitating condition for their serialization.

If you read the `perf_event_open()` documentation with this in mind, you'll see this option:
> **watermark**:
> If set, have an overflow notification happen when we cross the wakeup_watermark boundary.  Otherwise, overflow notifications happen after wakeup_events samples.

Well, that looks promising.
Let's add some more parameters to our configuration:
```
    .watermark = 1,
    .wakeup_watermark = 1,
```

One must ponder whether this configuration creates an even stream which is overly chatty.
After all, if these information events must be configured to be flushed, you'd think there's some value in not flushing them right away.
A mystery for another day.


##### Perf Events and Where to Find Them
The `perf_event_open()` manpage describes a number of structs, each of which defines how various events are represented in a ringbuffer.
Although they are presented in a manner analogous to C structs, don't be fooled--in the general case, they absolutely cannot be represented as structs as they are given.
What's even worse is that certain elements may be elided depending on the overarching `perf_event_open()` configuration.

How bad is it?  Let's start out with a `comm` event.
```
struct {
    struct perf_event_header header;
    u32    pid;
    u32    tid;
    char   comm[];
    struct sample_id sample_id;
};
```

Everything here is pretty normal, but check out `struct sample_id`:
```
struct sample_id {
    { u32 pid, tid; }   /* if PERF_SAMPLE_TID set */
    { u64 time;     }   /* if PERF_SAMPLE_TIME set */
    { u64 id;       }   /* if PERF_SAMPLE_ID set */
    { u64 stream_id;}   /* if PERF_SAMPLE_STREAM_ID set  */
    { u32 cpu, res; }   /* if PERF_SAMPLE_CPU set */
    { u64 id;       }   /* if PERF_SAMPLE_IDENTIFIER set */
};
```
Since we didn't define any of these values (we haven't and won't discuss proper sampling in this discussion), the `sample_id` struct is 0--totally elided.

##### Early to Bed, Early to Rise
One last thing.
This one is optional, but I find it helps to exercise a little bit of control--especially if you have infrastructure that must be orchestrated independently of `perf_event_open()`.
Add this to your configuration:
```
    .disabled = 1,
```
And then, when you're ready to start, do this:
```
    ioctl(fd, PERF_EVENT_IOC_ENABLE, 0);
```
All this does is ensure that the onset of event generation can occur after whatever it is you might need to do first.


#### Mapping Twice, Again
Now that we've gone back and properly showed our work, let's put it all together for a minimal example.
The interfacing code has been described above in bits and pieces--here's how the caller might be structured.
```
int main(int argc, char **argv) {
    PerfEventConfig *config = setup_perf_event();
    enable_perf_events(config);
    prctl(PR_SET_NAME, "poop", 0, 0, 0);

    struct perf_event_header *event = get_next_event(config->buffers, config->fds, config->n);
    struct perf_event_comm *comm = (struct perf_event_comm *)event;

    // Check if we got an event
    if (event == NULL) {
        printf("Somehow we didn't get an event?\n");
    } else {
        printf("Got event of type %d\n", event->type);
        printf("Comm: %s\n", comm->comm);
    }

    return 0;
}
```

And what do we get on stdout?
```
Got event of type 3
Comm: poop
```
Wonderful.

And, for completeness, the strace.
I'll walk through the different phases.

First, the `perf_event_open()` itself.
I'm going to render the configuration line-by-line, because if you try this at home, you may want a full reckoning of what _I_ did to make it work.
```
perf_event_open({type=PERF_TYPE_SOFTWARE,
 size=PERF_ATTR_SIZE_VER7,
 config=PERF_COUNT_SW_DUMMY,
 sample_period=1,
 sample_type=0,
 read_format=0,
 disabled=1,
 inherit=0,
 pinned=0,
 exclusive=0,
 exclude_user=0,
 exclude_kernel=1,
 exclude_hv=1,
 exclude_idle=0,
 mmap=0,
 comm=1,
 freq=0,
 inherit_stat=0,
 enable_on_exec=0,
 task=0,
 watermark=1,
 precise_ip=0 /* arbitrary skid */,
 mmap_data=0,
 sample_id_all=0,
 exclude_host=0,
 exclude_guest=0,
 exclude_callchain_kernel=0,
 exclude_callchain_user=0,
 mmap2=0,
 comm_exec=0,
 use_clockid=0,
 context_switch=0,
 write_backward=0,
 namespaces=0,
 ksymbol=0,
 bpf_event=0,
 aux_output=0,
 cgroup=0,
 text_poke=0,
 build_id=0,
 inherit_thread=0,
 remove_on_exec=0,
 sigtrap=0,
 wakeup_watermark=1,
 config1=0,
 config2=0,
 sample_regs_user=0,
 sample_regs_intr=0,
 aux_watermark=0,
 sample_max_stack=0,
 aux_sample_size=0,
 sig_data=0},
 0,  // PID (self)
 7,  // CPU Id
 -1, // group FD
 0) = 10
```

Next up, our ringbuffer mapping trick, in all of its glory.
Remember we map the whole space, then map the ringbuffer (`1 + 2^n` pages) to the end, then we map the same to the front so it overlaps the second map's metadata page:
```
mmap(NULL, 36864, PROT_NONE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7fca19151000
mmap(0x7fca19155000, 20480, PROT_READ|PROT_WRITE, MAP_SHARED|MAP_FIXED, 10, 0) = 0x7fca19155000
mmap(0x7fca19151000, 20480, PROT_READ|PROT_WRITE, MAP_SHARED|MAP_FIXED, 10, 0) = 0x7fca19151000
```

Then, a sequence of `ioctl()` operations to enable the events:
```
ioctl(9, PERF_EVENT_IOC_ENABLE, 0)      = 0
```

We change the name of the process in order to generate an event.
This isn't necessary except that we're tracking COMM events, and this creates one such event.
```
prctl(PR_SET_NAME, "poop")              = 0
```

Later on, the application code does whatever it needs to do.
Finally, we enter a `poll()` loop to wait for events.
You'll note that we'll be watching 8 events--why?
Because I have 8 cores in this VM (I culled duplicate `strace` entries above).
```
poll([{fd=3, events=POLLIN}, {fd=4, events=POLLIN}, {fd=5, events=POLLIN}, {fd=6, events=POLLIN}, {fd=7, events=POLLIN}, {fd=8, events=POLLIN}, {fd=9, events=POLLIN}], 7, -1) = 1 ([{fd=7, revents=POLLIN}])
```

See that we got a `POLLIN` type event for file descriptor 7?
Here's the output for that:
```
write(1, "Got event of type 3\n", 20)   = 20
write(1, "Comm: poop\n", 11)            = 11
```

### Your First Event, a Review
Where did we come from?  Let's recap.
1. `perf_event_open()` has no glibc wrapper; gotta call it directly
2. If you want to use a ringbuffer, you can use a cool trick to handle wraparound
3. Oftentimes, you have to map one ringbuffer per core; and that means one `perf_event_open()` per core
4. For groups with no sampling event, need to set a watermark
5. File descriptors become hot when they're ready to eat
6. Be careful deserializing!

This isn't much, to be honest.
It's not _incredibly_ useful to be able to track a few informational events.
But, many of the things I went over are massive stumbling blocks for first-time users, and it can be quite painful to get started.

By the way, the source code I referenced can be found [here](https://github.com/sanchda/systems_experiments/blob/main/perf_event_open/src/main.c).
