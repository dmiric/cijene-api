## Brief overview
This guideline establishes a strict rule for file modification operations to prevent errors caused by outdated file content assumptions.

## Development workflow
-   **Read Before Write/Replace:** Always read the target file immediately before attempting any `write_to_file` or `replace_in_file` operation. This applies even if the content is believed to be known or was recently read. This ensures the operation is based on the most current state of the file, accounting for any external changes or auto-formatting.
