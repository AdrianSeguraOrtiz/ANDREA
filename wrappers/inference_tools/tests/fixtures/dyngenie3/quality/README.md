# Eight-gene diagnostic contract fixture

This fixture exercises stored-model and quality-output semantics. It has no
biological truth network and is not evidence of predictive accuracy or cost.
All eight genes are candidates, leaving seven nonself candidates per target;
top-five stability therefore does not equal one merely by candidate count.

Three independent synthetic longitudinal series have coordinates
`[0, 0.5, 1.5, 3, 5, 8, 12]` hours. For zero-based gene `i`, series `s` and
observed time `t`, expression was generated with Python `math` as:

`12+i+2*sin((i+1)*0.37*t+s*0.43)+cos(t*0.11*(i+2)-s*0.31)+0.17*s*t+0.03*t*t*(i%3)`.

Series q0/q1 are labelled control and q2 treated; these labels only demonstrate
metadata boundaries. No transition crosses a series. Metadata rows are reversed
to test identifier joins. Biological unit qN identifies the corresponding
series; every observation has one technical measurement labelled tech1.

Four independently labelled synthetic steady-state columns use
`11+i+0.4*j+cos(0.6*(i+1)*(j+1))`, zero-based sample `j`. Their gene rows are
reversed to test alignment. These arbitrary contract values are not a simulated
equilibrium or a claim of physiological validity. They exercise the native
steady-state branch under the explicitly declared common arbitrary scale.

The adjacent `quality_one_sample` fixture retains only the first two q0 visits,
with the same eight genes. It yields one derivative training row. An empty VIM
and undefined Pearson score are expected; jitter-based upstream stability may
still be positive. This behavior is retained and is not a confidence estimate.

The verifier's five additional cases retain models and recompute VIM, OOB
Pearson and top-five stability inside the pinned image without refitting. It
uses the same per-target jitter seeds and preserves undefined values as null.
This fixture does not change the upstream metric or fix its scientific limits.
