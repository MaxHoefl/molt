# Evaluating the agentic tools

Unit tests prove the plumbing is right. They cannot tell you whether the model is
any good, because the model is stubbed. Evals answer the second question, and
they are a different kind of test: slower, non-deterministic, costing money, and
graded rather than binary.

The two are separated so that neither weakens the other.

| | Unit tests | Evals |
|---|---|---|
| Model | `ScriptedChatModel` (fake) | whatever `MOLT_SERVER_<TOOL>_LLM_MODEL` names |
| Runs | every commit | when you change a prompt, a schema, or a model |
| Asserts | plumbing: prompts, parsing, guard rails, concurrency | judgment: recall, precision, calibration, groundedness |
| Cost | none | one LLM call per case that needs one |

One suite per agentic tool, each a package of the same five modules — `dataset.py`,
`scorers.py`, `runner.py`, `test_harness.py`, `test_evals.py` — over the scaffolding
in `scoring.py`, which is what makes five suites' scorecards read the same way.

| Suite | Tool | Cases | Skipped by the tool without an LLM call |
|---|---|---|---|
| `security/` | `assess_security` | 9 | packages with no advisories |
| `license/` | `assess_license` | 21 | a dependency under the project's own license, or with none to resolve |
| `maintenance/` | `assess_maintenance` | 13 | packages with no measurable signal at all |
| `triage/` | `triage_dependencies` | 8 | a project where every package is clean |
| `replacement/` | `find_replacement` | 4 | a replacement already validated for this project |

```bash
uv run pytest                                    # unit tests only; evals are deselected
uv run pytest -m eval                            # every eval, one test per case
uv run pytest -m eval tests/evals/license        # one suite
uv run python -m tests.evals.security.runner     # evals, as a scorecard
uv run python -m tests.evals.license.runner
uv run python -m tests.evals.maintenance.runner
uv run python -m tests.evals.triage.runner
uv run python -m tests.evals.replacement.runner
```

Two suites need a word of explanation because they are not shaped like the other
three.

**`triage/`** does not score routing. The branch a package lands on is decided by
rules in `src/tools/triage_dependencies.py` and pinned exactly by unit tests, because
that decision leads to a file being edited. The suite scores what the manager agent
actually contributes: the merged rationale, and the discipline of escalating to
HUMAN_REVIEW *only* when the specialists genuinely conflict.

**`replacement/`** scores a process rather than a judgment, and it runs against a
frozen registry (`registry.py`) rather than the live PyPI — otherwise the model and
the internet would both be varying and the number would mean nothing. Half its
scorers ask "did it find the right thing?" and half ask "did it earn the answer?",
because a recommendation is the output a user is most likely to act on without
checking, and right-by-luck must not score the same as right-by-verification.

## The loop

1. **Label a case, not an output.** Every case in `dataset.py` carries the input
   and the verdict a competent analyst would reach *from that input alone*. No
   case depends on knowledge the model was not given — for the license suite that
   means every label is derivable from the compatibility matrix the agent is
   handed — so a failure is always the model's, never the dataset's.
2. **Score fields, not strings.** `scorers.py` asks a dozen narrow questions
   instead of one broad one, so a regression names a behaviour — "precision fell"
   — rather than "the eval went down".
3. **Write the case before the fix.** When a model surprises you, add the case
   first and watch it fail. `probes` on each case records the failure mode it
   exists to catch, which is what stops the dataset from decaying into a pile of
   inputs nobody understands.
4. **Compare scorecards, not vibes.** Change one variable — the model, the
   prompt, the temperature — rerun, diff the two tables.

```bash
MOLT_SERVER_ASSESS_SECURITY_LLM_MODEL="anthropic:claude-opus-5" uv run python -m tests.evals.security.runner > opus.txt
MOLT_SERVER_ASSESS_SECURITY_LLM_MODEL="ollama:llama3.1"         uv run python -m tests.evals.security.runner > llama.txt
diff opus.txt llama.txt
```

## What the scorers measure

**Security.** `cve_recall` and `cve_precision` are the two that matter most, and
they fail in opposite directions: recall misses a real vulnerability, precision
invents one. `evidence_grounded` and `no_lure` catch the model reasoning from
memory instead of from the advisory it was handed.

**License.** `verdict_accepted` and `never_relaxes` are the pair to watch, and
only one of them is a compliance incident: a verdict stricter than the label
wastes a reviewer's minute, a verdict more permissive than the label ships
someone else's copyleft inside a product that cannot honour it. `never_relaxes`
therefore scores the direction of the error, not just its size.
`clause_names_the_license` catches a citation about the wrong license, which is
what a model reasoning from a vague memory of "copyleft" produces.

**Maintenance.** `never_downplays` is the same shape as `never_relaxes` and for the
same reason: calling a healthy package declining costs a reviewer a minute, calling
an abandoned one healthy is how a project ends up depending on something nobody will
patch. `evidence_cites_a_measurement` and `no_invented_signal` catch the model
answering from its impression of a package's popularity rather than from the six
numbers it was given.

**Triage.** `mentions_the_findings` requires the rationale to reach every finding the
case carries, which is what catches a manager that copies one specialist and forgets
the other two. `speaks_findings_not_branches` enforces that a plan entry explains
itself in the user's language rather than restating the schema.

**Replacement.** `no_unverified_candidates` counts how often the guard rail dropped a
package the loop never looked up — the cheapest possible detector for a model
inventing library names — and `used_the_tools` fails an answer produced without a
single lookup, however correct it happens to be.

**All five.** `no_corrections` is the rawest signal — it counts how often the
verification layer in the tool had to overrule the model, so it measures model
quality *before* the guard rails hide the problem. `no_action_language` enforces
the architectural rule that a specialist characterizes risk and never recommends
an action.

The license guard rails are asymmetric on purpose, and the eval reflects that: the
compatibility matrix is a *floor*, so an agent may escalate a row and may never
relax one. A cleared copyleft dependency shows up as a correction (the matrix put
the verdict back) rather than as a wrong verdict — which is why `no_corrections`,
not `verdict_accepted`, is what catches it.

## The harness must be able to fail

`test_harness.py` runs the scorers against deliberately wrong scripted models and
asserts that each one is caught. An eval suite that cannot fail measures nothing,
and this is the cheapest way to keep that honest.

## Next step

`enrich_dependencies` already caches, so the eval inputs are stable and free.
The natural extension is to log each run to Opik (already a dependency) so
scorecards accumulate over time instead of living in your shell history.
