## Brief overview
This guideline specifies the preferred method for testing the AI chat endpoint.

## Chat Testing Workflow
-   **Pre-test Check:** Before initiating chat tests, review `service/routers/v1/chat.py` to understand the current chat logic and AI tool integrations.
-   **Language Requirement:** All chat messages/questions must be in Croatian, as the AI model is configured for Croatian language processing.
-   **Automated Responses:** When testing, you may automatically answer the chat up to 3 times to simulate a short conversation flow. You should wait for the response for the first question and then answer it.
-   **Preferred Method:** Always use the `make chat` command for testing the chat endpoint.
-   **Required Parameters:** The `make chat` command requires the following parameters:
    -   `MESSAGE`: The user's message to send to the chat.
    -   `USER_ID`: The ID of the user initiating the chat (e.g., `1` for the test user).
    -   `API_KEY`: The API key for the specified user (e.g., `ec7cc315-c434-4c1f-aab7-3dba3545d113` for user ID 1).
-   **Example Usage:**
    `make chat MESSAGE="Hello, what products do you have?" USER_ID=1 API_KEY=ec7cc315-c434-4c1f-aab7-3dba3545d113`
-   **Troubleshooting:** If an "Unknown API key" error occurs, ensure the user's `is_active` status in the database is `TRUE` and restart the API service to clear any cached authentication data.
