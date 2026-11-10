---
layout: post
date: 2025-01-29
title: "userfaultfd"
tags: systems-programming, linux, security
---

The internet is abuzz with interesting words to say about the latest and greatest developments in machine learning.
My feed is full of intelligent-sounding people making wildly opposed, intelligent-sounding predictions.
I recall those Kaggle competitions where ensembles of linear models would beat everything else.
But also, some where they didn't.
Right now, my ensemble of experts isn't converging.
What am I supposed to feel about the future without an influencer guiding my mind's heart?

That said, it's not like I have anything special here--my predictive powers are of a different sort.
More circumstantial.
Less consequential.
Worse.

For instance, years ago--sometime after kernel 4.3--I predicted that the unassuming [userfaultfd\(\)](https://man7.org/linux/man-pages/man2/userfaultfd.2.html) would become a major part of _all_ serious cloud-deployed services where page table was used for IO (in favor of direct memory access).
I trusted survival of the fittest.
The throughput characteristics of network storage are one thing, but latency is another, and the kernel with its default 32-page linear lookahead just wasn't going to cut it on a variety of workloads.
Ah, but with `userfaultfd()`, you can change that.
It worked great.
I had the receipts.

Nope, very wrong.

Still, it's a fun little syscall, so let's talk about what we can do with it.


# It's not your fault

Without a whole lot of narrative setup, let's assume you have an application doing the moral equivalent of this.

```
  int fd = open("my_data.dat", ...);
  size_t sz = (uhhh fstat?)
  unsigned char *my_data = mmap(..., fd, ...);
  for (unsigned char *p = my_data; ...) {
    ...
    if (p[i] == ' ') ...
```

In other words, you're doing a linear rip through memory-mapped data.:q

# ... Until it is
