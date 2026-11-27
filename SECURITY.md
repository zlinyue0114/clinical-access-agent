# Security

This is a research demonstration that uses synthetic data only. If you find a way
to violate one of the invariants in `DESIGN_SPEC.md` (for example, reading a record
outside the clinician's care team), please open an issue with a reproducible
description. Such reports are treated as test cases and added to the eval suite.

Known threats, controls and open risks are listed in
[`docs/threat-model.md`](docs/threat-model.md).

Never add real patient data to this repository, its issues or its pull requests.
