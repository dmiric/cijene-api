## Brief overview
This guideline defines the preferred workflow for testing, emphasizing self-sufficiency in checking test outputs.

## Testing Workflow
-   **Automated Log Checking:** After executing a test command, automatically read the relevant log file (e.g., `./logs/test-output.log`) to analyze the results.
-   **Self-Sufficiency:** Do not ask the user to provide the content of log files; retrieve them directly using the `read_file` tool.
