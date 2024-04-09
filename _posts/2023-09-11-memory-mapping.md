---
layout: post
date: 2023-09-11
title: "Memory Mapping"
tags: systems-programming historical
---

I think some of us suffer from a delusion that esoterica brings power. To those people, knowing something about Einstein's mother's musical affinity inspires a _feeling_ that one has gained a familiarity with the man . This myth has found repetition in many a fantasy story, where some trivial knowledge of a thing, such as its name or origin, grants control over it. Perhaps in our distant past our general level of understanding was so meager that any accessible fact was a form of power, of magic. The myth persists into the present day, where it is almost entirely useless. I am a lifelong sufferer of this disease.

This article sketches some high-level notes on the developments to the x86 architecture that led us from the segmented memory model of the 8086 to the nuanced (but, to the developer, flat!) memory model we see in x86_64 today. This maybe has some trivial value for students of the history of the craft, and _perhaps_ some incidental worth for those of us who do low-level development. But, again, I wrote this because I was cursed to, and your reading of these words is a misfortune.

## Memory of 8086

Any hardware platform has to strike a balance between the physical reality of the hardware itself and the conceptual model it asks the developer to adopt in its utilization. It's convenient to think that contemporary general-purposes CPUs provide the "default" abstraction, but it's worth remembering that the present situation was created by a process of evolution, iterating through a number of predecessor devices which each bore different memory models between themselves and the modern day. For example, the relatively flat memory model we enjoy today on x86_64 is a descendant of the segmented memory model brought by earlier incarnations of the x86 family.

### Segments

The 64-bit address space offers a lot of room for addresses--so much so, that the physical implementation of the address bus on amd64 is less than 64-bits wide (Why? Because good luck powering 18 exabytes of storage!). Back in the 16-bit days of 8086, however, the opposite was true. The 8086 had 16-bit registers, but a 20-bit address bus. In order to make up the extra 4 bits, some registers (`CS`, `DS`, `SS`, and `ES`) could be used to encode a "segment," which is a region of memory within which the 16-bit offset would be computed. High-level languages handled this detail for the most part, but segmentation still played a role in the mental model one used to develop software.

Pause for reflection. This dynamic is one we see frequently--at particular points in history, within particular contexts, specific bottlenecks or overheads dominated the behavior of certain systems. Today, opposing forces are at play (and the landscape continues to change). In other words, advances in hardware are non-uniform with respect to consumption. This is one reason why those of us in performance engineering still have jobs.

Of course, 8086 wasn't the only show in town. Other processors had existed for some time, and there's little doubt Intel engineers drew from the experience of mainframe implementors. For example, when GE had a computer division and devised the GE-645, which ran Multics, it came with forms of both segmentation _and_ paging. This allowed developers to implement one of the first virtual memory systems in ~1969, a strong decade before the changes we're discussing. That said, the 8086 used segments, multics processors used segments, and many processors between, before, and since used segments. However, the popular contemporary architectures do not really expose such an abstraction to end-users, and hence the modern zeitgeist is entitled in taking a flat memory model for granted. Then, it is a little bizarre that a ubiquitous experience for today's systems programmers--the _segmentation fault_--owes its name to the idea of attempting to illegally access a segment. Maybe it's not _really_ so bad, but kinda.

When I first learned to program (in Real Mode on the 80386, I think, although substantially _after_ that era waned in significance), I took this as a mere cost of doing business. Why do you have to specify a segment when you access memory? Because that's how it works--to some extent, the syntax becomes a representation of the hardware and its limitations. But, in so becoming, obscures the possibility for other arrangements.


### Protected Segments (8026)

The years 1981/1982 were marked by several meaningful changes in the home/business PC landscape. There was a growing awareness and need for multitasking operating systems (for _home_ and small-business users), in which the OS would run multiple processes concurrently and switch between them as-needed. This in turn mandated a stricter security model. Moreover, as applications became more complex, the burden of dealing with segmented memory was an increasingly powerful handicap against those developing on such platforms. Whether or not these were the causal reasons for its introduction, the 80286 was equipped with a powerful tool to tackle these concerns: protected mode.

Coarsely, the 80286 protected mode was a security feature. It allowed the OS to reside inside a high-privilege bubble within the processor, preventing usermode applications from accessing privileged resources. The primary abstraction for this interface was at the level of the segment--some segments would be privileged--which in turn were defined in a lookup table called the "descriptor table." Basically, certain processes would have access to certain segments, while the kernel would get exclusive access to other segments. With this, the end was spelled for the simple memory access of old--now, developers had to engage in a more structured memory system.


#### Descriptor Tables and Segment Access

In the 80286 protected mode, the concept of segments was still retained from the 8086, but the overall context and usage of segments and memory was more structured. Segments were stored in descriptor tables, such as the Global Descriptor Table (GDT) and the Local Descriptor Table (LDT). These tables contained entries that defined the characteristics of each segment, including its base address, size, access permissions, and privilege level. The descriptor tables worked to define segment access and permissions. Each entry in the descriptor tables had a segment selector, which was a unique identifier for a particular segment. When a segment was accessed, the segment selector was used to locate the corresponding entry in the descriptor table.

The access permissions defined the operations that could be performed on the segment, such as reading, writing, and executing. The privilege level, ranging from 0 to 3 (these correspond to "ring 0" etc.), determined the level of access allowed to the segment. A privilege level of 0 indicated kernel-level access, while a privilege level of 3 indicated user-level access. I'm not 100% sure how it worked across operating systems, but my understanding is that rings 1 and 2 were typically reserved for drivers and such--pieces of code that implemented the user interface to peripheral hardware resources, and as such were not privileged as highly as the kernel (ring 0), but still needed access typically forbidden to the user.


### Better Bus, More Modes (80386)

If you're still with me, this is the part where the x86 family won itself a flat memory model by leveraging some of the groundwork that was set with the 80286. I think this is actually quite a fascinating case study in how generalizing an interface allows you to change fundamental implementation details.

The 80386 brought significant changes to the x86 architecture, including the introduction of a full 32-bit architecture with a 32-bit address bus. This meant that the previous restrictions imposed by segmented memory started to feel a bit restrictive. Developers wanted a more flexible and efficient memory model, and that's where paged memory came into play. Paging, in simple terms, allows addresses to be used without further qualification by the calling process. Instead, the CPU (through the Memory Management Unit or MMU) looks up the significance of the address in a data structure called the page table. This eliminates the need for individual segment specification and enables a more direct and efficient memory access.

In the 80386 architecture, the interplay between segments and paged memory was more flexible compared to earlier versions. The user did not have to manually select the memory mode, as the CPU automatically switched between segmented and paged memory depending on the operating mode. The OS would still specify permissions at the level of segments, but the CPU could then look up the segment (and hence, the relevant permissions) on-the-fly.

The introduction of paged memory in the 80386 architecture also brought the concept of virtual memory and demand paging. Virtual memory allows for a larger address space than the physical memory available, and demand paging allows for efficient memory usage by loading only the required pages into physical memory when needed. This description is scant on details, since I'd like to write about the practical implications of these concepts in a future post.

Overall, the 80386 architecture provides a more advanced memory management system compared to earlier versions, with the ability to switch between segmented and paged memory modes, specify permissions at the segment level, and perform dynamic memory translations for efficient memory usage.

## That's it

If you got this far, thank you and I'm sorry.
