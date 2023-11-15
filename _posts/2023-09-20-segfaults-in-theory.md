---
layout: post
date: 2023-09-20
title: "Segfaults in Theory"
tags: systems-programming, segfault, defect
---

I'm going to make a brazenly inflammatory statement and play it off as a deeply held conviction. Unfortunately for both of us, my conviction here is heartfelt and it may be for the better if we both just assume otherwise.

I love segfaults.

Let me be clear. I don't prefer erroneous code over non-erroneous code. I don't prefer for an application to be able to read arbitrary memory. I don't prefer applications to crash and burn. I don't love segfaults in such a way that I want there to be more segfaults in the world. What I love and miss is a form of innocence: a time when an immediate and instant crash, with all context intact, was an _actionable_ crash. But that was then, and this is now. Now, what I want to talk about is the behavior of segfaults and what to do with them.

## Biographical notes

My experience with segfaults isn't very relatable, and if some of my reasoning seems alien--it's because it is. I'm a first-generation American, but only a second-generation programmer. I learned x86 assembly language from my dad, who wielded MASM with a deft and delicacy I have yet to reproduce with the languages with which I consider myself expert. When I became ready to learn about segmentation faults, he taught me that they arose from confusion--state management gone awry. Either, the CPU has begun interpreting _data_ as _code_ (a folly many of us will never, ever reproduce in our own working lives) or a jump-type operation has landed on the wrong byte, causing the CPU to interpret opcodes incorrectly (x86, as you may remember, has variable-width opcodes and different codes have a different number of arguments), which decomposes to the former case.

This isn't wrong, but it isn't comprehensive, and it doesn't relate precisely to the sequence of operations which is typical for a contemporary developer. Just the same, since I was a child when I learned this myth, it stuck with me. Even today, my first split-second of any segfault analysis is colored by the impression: "Ah-ha, so now we're trying to execute _data_!"

That said, this wasn't as bad as it could have been as far as myths go. In reality, when we are at our most innocent and vulnerable, many of us acquire a collection of profoundly incorrect lies about how computers work. I never did learn whether my dad understood anything more about segmentation faults, but I don't think that was one of his deepest concerns. Even though we think of assembly programmers as living "close to the hardware," segmentation faults were often thought of as mundane runtime errors that were quickly identified by tests and corrected. I know for a fact that some folks thought of segfaults the same way some of us think of compiler errors--usually annoying, but ultimately helpful, indicators of program incorrectness.

## Why is it called a segmentation fault?

Completely irrelevant. Unask the question.


## What causes a segmentation fault?

A segmentation fault occurs whenever an application attempts to de-reference an address for which the process is not permissioned. The phrase "not permissioned" is actually doing a lot of heavy lifting in this description, so I'll break it down into some examples. First, some discussion on paged/virtual memory.


### Some discussion on paged/virtual memory

We're going to limit our discussion to processors that used paged memory. If you don't have one, you'll know, and you might even be able to stop telling everyone about it someday.

Virtual memory is a memory management technique that abstracts the logical memory layout of a process from physical memory. It doesn't change the fact that applications still rely on linear memory, and indeed the precise layout may have certain somewhat deterministic features, but the addresses themselves should only be mappable back to physical memory by the CPU and OS working in concert. Under the hood, the management of virtual memory involves the transformation of virtual addresses into physical ones, a process which relies heavily on the use of Page Tables. A Page Table is a data structure used by the OS to store the mapping of virtual addresses to their corresponding physical addresses. The quantum of this mapping operation is the page--usually, but not exclusively, 4096 bytes.

One might conceptualize this by imagining each process running on the system to be assigned its own private address space, allowing it to function as if it has exclusive use of physical memory. By providing this logical view, the system can efficiently manage and allocate memory resources, enhance security, and enable programs to run without concerning about physical memory's actual capacity or data placement.


### Some more discussion, paged memory in practice 

One important realization from the note above is that the unit of permissioning is the page. In other words, when a user requests an address, the OS has to round that address down to the start of the containing page in order to compute permissions and availability. This means that the state of the process as a component of the system might be a little bit different than the conceptual state of the process from a developer's thinking about it. This may not be an obvious distinction, so let's talk about it.

No matter the dominant mental model a developer may use to reason about objects in their programming environment-du-jour, those objects are almost always persisted in computer random access (read or write) memory (to use the term favored by the ancients). And while we're at it, since most contemporary systems use the Von Neumann architecture, the same should be said about the machine instructions which cause those accesses to occur. However, many programming languages give us the tools to avoid having to think about those addresses ourselves--for instance, we don't often chase pointers in Python.

Just the same, our environments and runtimes _do_ have to manage these addresses, and sometimes in fact we do as well. Take for example the following snippet of C:

```C
#define PAGE_SIZE 4096

uintptr_t align_to_page(uintptr_t ptr) {
  return ptr & ~(uintptr_t)(PAGE_SIZE - 1);
}
...
  unsigned char *buf = malloc(10);
  unsigned char *buf_begin = align_to_page(buf);
  unsigned char *buf_end = buf_begin + 4095;
...
  *buf_begin='\0';
  *buf_end='\0';
```

Conceptually, when we allocate memory with `malloc()`, we are advised to keep our extremities within the enclosure at all times--we have to respect the range of bytes we requested. This is no less true in the horrible example, which will almost certainly break most applications, perhaps even disasterously. However, the point of the exercise is to know what instantaneous operations will segfault. This code may _cause_ an application to segfault later, but it will not cause it to segfault as a by-product of the execution of those lines.

The point is: when you allocate, you get a page. When you apply protections, they apply to a page.


### For your entertainment: segfaults

Let's talk about some segfaults now.

#### Null page

Owing in part to history, the most common type of segfault probably consists of trying to de-reference an address which _itself_ (as an address) has not bee initialized.  For instance.
```
struct MyStruct {
    int foo;
    int bar;
};

struct MyContainer {
    struct MyStruct *first;
    struct MyStruct *second;
};

int main() {
    struct MyContainer my_container = {0};
    my_container.second->foo = 42;
    return 0;
}
```

There are some C-isms here which may not be immediately evident. `struct MyStruct *first` defines a _pointer_. This is an address. That pointer can be used to reference the `foo` or `bar` members of a `MyStruct` object through indirection; `first->foo` for example (and this would be a valid LHS or RHS). Realize that this is a mistake--we initialized an object (`my_container`), but that _does not_ initialize the pointers inside of it. In fact, this is an _incredibly common_ mistake. Perhaps even one of the single most common mistakes in all of computer science (second only to getting into computer science in the first place). Keep this in mind through the next bit.

I'm a reductionist when it comes to errors, so let's reduce this. Here's a sketch for how (my personal mental model of...) a non-optimizing compiler might represent `main()`:

```
mov [rsp-0x18], 0x0  ; 0-initialize my_container
mov [rsp-0x10], 0x0  ; 0-initialize my_container
mov rax, [rsp-0x10]  ; get the address of my_container.second->foo
mov [rax], 0x2a      ; ... then put 42 into that address
```

Quantities in brackets are to be thought of as addresses--they get dereferenced. So, we take the value `[rsp-0x10]` (which is the value of the stack pointer, minus `0x10` bytes) and put zero in it.  `rsp`, the stack pointer, is assuredly a valid address, as is `rsp-0x10`.  Those operations are normal and beyond scrutiny--we're merely allocating an object on the stack.  The problem is this one:

```
mov [rax], 0x2a      ; ... then put 42 into that address
```

Recall that at this point `rax` is 0, so `[rax]` is the value at address 0. Fine, addresses are numbers, so what's the problem? Here's the deal. Humans made computers, but there is a progression to these things. The first time you make something, it's an accident.  The second time you make something, you did so so knowingly, but at your own peril--and at the mercy of cruel gods in need of entertainment. As you create, you learn. Every time a human makes something with a computer, they learn something.

When you make the most common mistake in computer science the first time, it's fine. Let's assume that an address is an address, the current process owns the range `0x0000 - 0x0FFF` (the 0-page or "null page"). We just wrote `42` there.

But we make it again.
```
my_container.second->foo = 1;
```

`second` is also zero-initialized. We just over-wrote `42` with `1`. Our application data is now invalid, and it is _very challenging_ in an arbitrary application to figure out what happened. As you might imagine, this is also prone to numerous security issues, not least of which is the simple predictability of reads.

Accordingly, the most common mistake in computer science has been given one of its harshest penalties: applications cannot map the null page, and null pointer dereferences (almost) always segfault. (I say "almost" because you can imagine an impossibly large struct and an extremely unfortunate virtual memory layout that might break such a statement, although I somewhat doubt such a situation can be realized on a contemporary, mainstream system).

#### Page permissions

Here's a trick you see in some hardening techniques. You take a normal memory-map, then use `mprotect()` to strip all of the permissions out of a range of pages. Why? Because you anticipate someone naughty might try to do something, like start out in your dynamic library and crawl forward until they reach something useful. By throwing in a guard page, you cause that process to segfault.

You may not actually run into this in practice, but I mention this example to illustrate a few points:
* In several cases, segfault protection can be used defensively
* You can have a valid map that still throws a segfault on access

#### Invalid access

Let's say I make a mistake.

```
char *foo = mmap(...);
munmap(foo, ...);
memcpy("Hello", foo, 6); // 5 + 1 = 6, but also a segfault
```

Here I _had_ a perfectly valid page given to me by `foo` (assuming that the mapping was granted), but I accessed it after releasing it. This anachronistic access will almost always result in a segfault. "Almost" because you might have a multithreaded application, and mappings are handled (on Linux) at the level of the process rather than the thread.

##### Intuition check: invalid access II

"Cool," you say. "Nobody who doesn't deserve a segfault in the first place calls `mmap()` for allocations. Let's have a practical example."

```C
char *foo = malloc(...);
free(foo);
memcpy("Hello", foo, 6);
```

Here's the problem. In order for your application to allocate memory, it needs to be given memory by the OS. Your allocator (`malloc()`) does this request on your behalf, but in so doing it may not call `munmap()`. That region may still be hanging around, waiting for reuse (because syscalls are expensive, but arithmetic is cheap). This is miserable application behavior, it _may_ indeed throw a segfault, but it doesn't _have to_.

### This page left intentionally blank

I'd like to put more stuff here someday.
