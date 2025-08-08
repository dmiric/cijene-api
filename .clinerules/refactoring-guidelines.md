## Brief overview
This guideline outlines preferences and best practices observed during the recent database refactoring task, focusing on architectural patterns, debugging, and file modification strategies.

## Architectural Patterns
-   **Facade Pattern**: Prefer using the Facade pattern for complex subsystems (e.g., database interaction) to provide a simplified, unified interface to the application layer.
-   **Repository Pattern**: Implement the Repository pattern to abstract data access logic, separating it from business logic. Each repository should be responsible for a single data domain.
-   **Abstract Base Classes**:
    -   Use a `BaseRepository` abstract class to define common connection management methods for all concrete repositories.
    -   Maintain a top-level `Database` abstract class to define the complete interface for the main facade, ensuring all its abstract methods are implemented by the facade.

## Debugging Strategy
-   **Container Logs**: When encountering issues with Docker containers (e.g., connection errors, application crashes), always check the container logs using `task logs:<service_name>` to identify the root cause.
-   **Error Traceback Analysis**: Carefully analyze Python tracebacks, especially `TypeError` and `AttributeError`, to pinpoint issues related to abstract method implementation or incorrect delegation in refactored code.
