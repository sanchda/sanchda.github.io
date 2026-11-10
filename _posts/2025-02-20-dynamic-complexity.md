---
layout: post
date: 2025-02-20
title: "Static and Dynamic Complexity"
tags: systems-programming, distributed systems, opinion
---

The contemporary zeitgeist fetishizes simplicity, and I believe it is mostly correct to do so.
Complexity is to be avoided when possible and scrutinized when not.
It is the origin of defects and the bane of velocity.
Being clever is hard, but it's harder to do so reliably.

Okay, so far I'm with it.

To frame it another way, code doesn't sit still.
If code could be written once and then remain fixed for all time, then maybe the tradeoffs could be a little bit different.
But that's not how it is--we're constantly battling and reshaping code to fit our needs.
We strive to write things that are _easy to maintain_.

Here's where I break from most you.
Modifying code is not the most important category of maintenance exercises.
Instead, I think it goes like this: defects, deployments, provisioning, then coding.
Season to your own taste, but this is mine.

Now, this might sound like a really edgy way of confirming the original premise.
I think it is intuitively correct to most people that managing complexity is a boon to all of those activities.
To an intermediate level--absolutely.
Idiomatic use of a language is almost certainly going to respond better in the ways I counted above than something sloppy or ill-conceived.



## Dynamic Complexity
In addition to sitting pretty in your text editor, code is executed.
It inspires an otherwise useless pile of

## Static Complexity
I think when people reason about code complexity, they're drawing from topics I'll call "static complexity."
That is to say, complexity in terms of the _representation_ of code.
There's a great formalization of this, [cyclomatic complexity](https://en.wikipedia.org/wiki/Cyclomatic_complexity), which is used by some folks to estimate test requirements for new code.

Obviously it's incredibly useful to think about this and manage it carefully.
If it is true that the presence of complexity makes code harder to manage, then if you manage code,
