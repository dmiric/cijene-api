## Brief overview
This guideline outlines preferences and best practices observed during the recent database refactoring task, focusing on architectural patterns, debugging, and file modification strategies.

## Architectural Patterns
-   **Facade Pattern**: Prefer using the Facade pattern for complex subsystems (e.g., database interaction) to provide a simplified, unified interface to the application layer.
-   **Repository Pattern**: Implement the Repository pattern to abstract data access logic, separating it from business logic. Each repository should be responsible for a single data domain.
-   **Abstract Base Classes**:
    -   Use a `BaseRepository` abstract class to define common connection management methods for all concrete repositories.
    -   Maintain a top-level `Database` abstract class to define the complete interface for the main facade, ensuring all its abstract methods are implemented by the facade.

## Debugging Strategy
-   **Container Logs**: When encountering issues with Docker containers (e.g., connection errors, application crashes), always check the container logs using `docker compose logs <service_name>` to identify the root cause.
-   **Error Traceback Analysis**: Carefully analyze Python tracebacks, especially `TypeError` and `AttributeError`, to pinpoint issues related to abstract method implementation or incorrect delegation in refactored code.

## File Modification Strategy
-   **Prefer `replace_in_file` for small, targeted changes**: Use `replace_in_file` for minor modifications to existing files.
-   **Use `write_to_file` for large or complex changes**: For extensive refactoring, creating new files, or when `replace_in_file` proves problematic due to exact match requirements, use `write_to_file` to provide the complete new content of the file.

## Testing Preferences
-   **CLI Test Execution**: Utilize provided CLI scripts (e.g., `test-scripts/send-chat.ps1`) for automated testing.
-   **Verification Steps**: Follow explicit verification steps outlined in testing guidelines (e.g., checking log files for tool calls and AI responses).
