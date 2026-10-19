# flow Generated Output Exception, version 1.0

Copyright (c) 2026 HugeMan <942295.xyz>

This is an Additional Permission under section 7 of the GNU Affero General
Public License, version 3 (the "AGPL"), granted by the copyright holders of
flow. It applies in addition to, and not in place of, the AGPL.

## Why this exception exists

`xdog-flow generate` does not emit a program that *calls* flow. It emits a
standalone Python module with parts of flow's own source **copied verbatim into
it** — the frontier transition kernel and its companions are inlined with
`inspect.getsource` precisely so that the generated module runs the same
scheduler as the interpreter, with no dependency on flow at run time.

Without this exception, the AGPL would follow those copied portions into every
compiled workflow, and a tool whose entire purpose is to hand you an artifact
you own would instead hand you an obligation. That is not the intent.

## 0. Definitions

**"flow"** means the workflow format, validator, interpreter, compiler and
tooling distributed under the AGPL in this repository.

**"Runtime Kernel"** means those portions of flow's source that flow's own
compiler copies into its output. At the time of writing these are the frontier
transition kernel, the checkpoint interceptor, the run-result envelope helpers,
the port-preview helpers, and the inlined tool registry, interpolation and
coercion helpers. The list may change; the exception applies to whatever flow's
compiler in fact copies.

**"Generated Output"** means:

- a workflow module produced by `xdog-flow generate`;
- a portable bundle produced by `xdog-flow generate --portable`;
- unit, timer and configuration files produced by `xdog-flow scheduling install`;
- any workflow definition (`workflow.json`, or an SVG carrying one) that you
  author or that a program authors on your behalf.

## 1. Grant

You may convey Generated Output under terms of your choice — including
proprietary terms, and including as part of a commercial product or a network
service — and neither the Generated Output nor any larger work based on it
becomes subject to the AGPL by reason of incorporating the Runtime Kernel.

This permission applies whether the compiled workflow definition was written by
a person or produced by a program, and it applies to output you generate for
yourself and to output you generate for others.

## 2. What this exception does not grant

This exception covers the Runtime Kernel **only as incorporated into Generated
Output by flow's own compiler**. It grants nothing with respect to flow itself.

Conveying flow, or any modified version of flow, remains governed by the AGPL in
full. In particular, section 13 of the AGPL continues to apply: if you offer
flow or a modified version of flow to third parties over a network, you must
offer those users the corresponding source.

Copying the Runtime Kernel out of flow by hand, or by any means other than
running flow's compiler on a workflow definition, is not Generated Output and is
not covered here.

## 3. Vendored packages

A portable bundle may also contain copies of separate packages, each under its
own licence; see the licence files distributed under `_vendor/`. Nothing in this
exception alters those terms, and nothing in those terms is granted by it.

## 4. Severability

If for any reason this exception is held unenforceable, the AGPL applies to the
Runtime Kernel as it would without this document, and the remainder of the AGPL
is unaffected.

---

*This document is a licence grant, not legal advice. It is modelled on the GCC
Runtime Library Exception, which addresses the same structural problem — a
compiler that copies parts of itself into what it compiles.*
