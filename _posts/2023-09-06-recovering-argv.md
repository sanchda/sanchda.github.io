---
layout: post
date: 2023-09-06
title: "From Whence Argv"
tags: C, POSIX
---

Let's play the game where we ask a silly question, but answer it earnestly:

> How can I get the value of `argc` and `argv` from outside of the `main()` function?

## Some notes on argv

On Unices, new process images are instantiated through a call to `exec*()`. This is a family of syscalls that take different kinds of arguments and mutate the current process into a new one. The exemplary case is `execve()`

```
int
execve(const char *path, char *const argv[], char *const envp[])
```

In other words, we tell `execve()` where the executable file resides and what arguments to give it. Subsequently, when we write applications, they begin their lives being given those arguments:

```
int
main(int argc, char *argv[])
```

Observe that `envp`--the _environment_ (or `environ`, the list of key-value pairs which are environment variables and their values) is passed to `execve()` and it is perfectly normal to access this data wherever you are in an application--even a library--with `getenv()` and the like. Contemporary developers often think of the environment as the context in which a process is executing, but not necessarily a part of the process itself. This is reinforced by the nature of the `getenv()`/`setenv()` interfaces, which can be called from anywhere (plus they look _transactional_, and we've been taught through repetition that transactions go somewhere else and mutate somebody else's state). Like any matter of convention, it doesn't have to be like this. Consider this alternative prototype for `main()`:

```
int
main(int argc, char *argv[], char *envp[])
```

Given that `argv` and `envp` came to us from the call to `execve()` which spawned our process, is it equally valid to say that `argv` is part of the context of execution as it is to say the same for `envp`?  I don't know, but unfortunately there's a bit of convention here we can utilize to answer our original question.

##  Environmental engineering

There are slight differences between Linux and BSD, but according to `man environ`, libc ships access to the pointer where the environment variables live:

```
extern char **environ
```

This is completely pointless trivia, except if we combine it with another absolutely pointless piece of trivia. When a process is initiated, it's the kernel that receives `argv` and `envp`. However, the process needs to use that information. Accordingly, `argc`, `argv`, and `envp` will be stored in sequence, and `environ` will point to _that_ copy of `envp`. In other words, we can go backwards from `environ` to get the information we wanted.

Now, this isn't portable behavior, but it's a convention on many popular contemporary systems.

```
extern char **environ; // need to tell the linker about this

typedef struct {
  int argc;
  char **argv;
} Args;

Args get_args() {
  char **argv = environ;
  int argc = 0;
  for (argv--; argc < 1024 && argc != (long)argv[-1]; argc++, argv--)
    if (argc  == (long)argv[-1])
      return (Args){argc, argv};
  return (Args){};
}
```

Conceptually, we're looking at something like this.
```
0x0000000000000000
...
argc
argv[0]
...
argv[argc]
NULL
envp[0] <-- environ points here
...
envp[n]
NULL
...
0x7FFFFFFFFFFFFFFF
```

We're starting at `environ` and looking one (aligned) address higher. Along the way, we count how many slots we've looked at. We know we've reached argc when interpreting the 8-byte sequence as an integer returns a value which is equal to the number of slots we've counted.

It might also surprise you to find that this code works perfectly well:

```
int main(void) {
  Args args = get_args();
  for (int i = 0; i < args.argc; i++)
    printf("argv[i]: %s\n", i, args.argv[i]);

  return 0;
}
```

In other words, we can extract the commandline arguments from within an application which itself did not make those arguments transparent to `main()` (if you're wondering about this, keep your eyes peeled for a later article on the system V ABI and its significance).

One more piece of trivia that comes in handy here, and before I spoil the surprise let me lead with some code and its output:

```
#include <stdio.h>

extern char **environ;
void *get_stack_top() {
      int _; return &_;
}

int main() {
      printf("environ diff: %lx\n", environ - (char **)get_stack_top());
        return 0;
}
```

On my machine, this prints:

> environ diff: 5f

When a process is instantiated, its virtual memory layout is backed by many kinds of mappings. There are a plethora of exotic and interesting backing types, such as userfaultfd and memfd, but for the purposes of discussion let's pretend that the mappings we have are file-backed (such as those regions related to dynamic libraries and the main binary), heap (anynomous mappings), and stack (also anynomous).

I'll leave the distinction between heap and stack for a different post, but in general it's a great place for the OS to stuff things that are outside of the management of the user. The code above takes advantage of the fact that the C language pushes scope-local variables to the stack.  Thus when we return the address to one such variable, we're actually returning an address near the "top" of the stack. Since it has a very small distance from such an address, `environ` is very obviously part of the stack.  Thus, when we navigate downward as we do (remember:  stack grows from high-valued to low-valued addresses), due to the dynamics of stack access we're unlikely to overflow.

There you have it. A pointless answer to a question nobody asked.


## Upping the stakes

Let's try to do something with this knowledge.

On many common and popular Unices, `ps -ef` will provide a listing of currently-running processes, alongside the full value of their commandline arguments. It doesn't matter how the process was launched--be it through a shell, via `popen()`, or even by a direct call to `execve()`--those arguments are visible to anyone who has the right to see them.

For this reason, the conventional wisdom is that secrets should never be passed into a process as an argument. Rather, they should be transmitted through some other means. Just the same, wouldn't it be cute to modify our arguments somehow?

One might imagine that commands like `ps -ef` speak to some kind of system-wide registry of process information. To many people, this implies that the kernel keeps this information handy. For better or worse, that's not how things really work. Rather, these registries are just reflecting the internal state of the processes they list. In other words, there is a place inside of your application where the instantaneous value of `argv` is encoded, and that place is checked by the kernel whenever someone asks for that information. From this understanding, it stands to reason that modifying this place might change how the arguments to our process are reported.

For most people, there is very little reason to ever frame this exercise, let alone follow through with it. But doing so is surprisingly straightforward:

```
#include <stdio.h>
int main(int argc, char **argv) {
  argv[0][0] = '*';
  print_ps();
  return 0;
}
```

This program will modify its own `argv` (a program must have a name, and that name is hopefully at least a single character) in a fairly trivial way.  It will then use `popen()` to shell out to `ps` (through the `print_ps()` helper function, which is included in the code link at the end) to verify that an external observer would see the same change. We didn't so so in the above, but it's trivial to verify that the `get_args()` procedure returns the same `argv` pointers as the one given to `main()`.

## What comes next?

One of the difficult things here is that strings from `argv` are packed, so it isn't completely obvious how to modify the process name or its arguments/number of arguments, except if those edits are less-than-or-equal-to the length of the object being edited. I might cover this a little in a future post.

## OK but how

Code in my [systems experiments repo](https://github.com/sanchda/systems_experiments/tree/main/argv).
