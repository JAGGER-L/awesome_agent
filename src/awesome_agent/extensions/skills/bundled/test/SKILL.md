---
name: test
description: Design and run focused validation for changes
allowed-tools: [ls, read_file, glob, grep, execute, edit_file, write_file]
---
# Test the behavior that matters

Translate the requested behavior into observable contracts and failure cases.
Choose the lowest test level that proves each contract, adding integration tests
only when a real boundary is involved. Avoid assertions on private structure when
public behavior is sufficient.

Run formatting, static analysis, and targeted tests in increasing cost order.
Report the commands actually run, their outcomes, and any meaningful risk that
remains unverified.
