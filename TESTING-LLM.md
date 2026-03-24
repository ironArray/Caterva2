# Testing the Server-Side LLM Integration from JupyterLite

This document describes how to test the new server-side LLM feature from a
JupyterLite notebook, using a staging Caterva2 wheel first so the production
wheel channel is not affected.

## 1. Publish a staging Caterva2 wheel

1. Push the branch you want to test to GitHub.

2. Open the GitHub Actions workflow:
   `Build and Publish Python Wheels for Caterva2`

3. Click `Run workflow`.

4. Select the branch you want to test.

5. Select `channel=staging`.

6. Run the workflow and wait for it to finish successfully.

## 2. Verify the staging wheel was published

Check the staging wheel channel:

- `https://ironarray.github.io/Caterva2/wheels-staging/latest.txt`

Optional metadata checks:

- `https://ironarray.github.io/Caterva2/wheels-staging/commit.txt`
- `https://ironarray.github.io/Caterva2/wheels-staging/ref.txt`
- `https://ironarray.github.io/Caterva2/wheels-staging/channel.txt`

Make sure the commit and ref match the branch you intended to test.

## 3. Start the Caterva2 server with the new backend

The notebook wheel only provides the client-side Python package.  The server
must also be running the new backend code from the same branch.

Before testing, ensure:

- the Caterva2 server is started from this branch
- LLM support is enabled in the server configuration
- the desired provider is configured
- the required provider API key is available in the server environment if using
  a real provider such as Groq

If you only want a lightweight backend smoke test, you can also configure the
server to use the `mock` provider.

## 4. Point the notebook to the staging wheel

In your JupyterLite test notebook, replace the Caterva2 production wheel
bootstrap with the staging URL.

Example:

```python
import sys

if sys.platform == "emscripten":
    import requests
    import micropip

    caterva_latest_url = (
        "https://ironarray.github.io/Caterva2/wheels-staging/latest.txt"
    )
    caterva_wheel_name = requests.get(caterva_latest_url).text.strip()
    caterva_wheel_url = (
        f"https://ironarray.github.io/Caterva2/wheels-staging/{caterva_wheel_name}"
    )
    await micropip.install(caterva_wheel_url)
    print(f"Installed staging wheel: {caterva_wheel_name}")
```

## 5. Open a fresh JupyterLite session

Use a fresh browser tab or a fresh notebook kernel before testing.  This avoids
reusing a previously installed `caterva2` wheel from the same Pyodide session.

## 6. Open the LLM test notebook

Open:

- `_caterva2/state/personal/cd46395a-3517-4c48-baba-186d14b0fd94/prova3.ipynb`

This notebook contains helper code for:

- creating a server-side LLM session
- sending prompts with `ask(...)`
- resetting the session
- deleting the session

## 7. Run the notebook cells

1. Run the bootstrap cell and confirm the staging Caterva2 wheel installs.

2. Run the LLM setup cell and confirm it prints an LLM session id.

## 8. Run smoke-test prompts

Use the helper functions from the notebook to test the main flow:

```python
ask("List the available roots")
ask("List datasets under @public/dir1")
ask("Show metadata for @public/ds-1d.b2nd")
ask("Show stats for @public/ds-1d.b2nd", show_trace=True)
```

Check that:

- the response text is returned
- the trace output lists the expected tool calls
- metadata and stats look correct

## 9. Test session lifecycle

From the notebook, test:

```python
reset_agent_session()
ask("List the available roots")
delete_agent_session()
new_agent_session()
```

Confirm that:

- reset keeps the session usable
- delete removes the current session
- a new session can be created afterward

## 10. Test authentication behavior

If login is enabled on the server:

- test from an authenticated JupyterLite session
- confirm the LLM session can be created and used
- confirm anonymous access is rejected when `llm_allow_public_access` is false

## 11. Check server-side behavior

While exercising the notebook, inspect the Caterva2 server logs and verify:

- requests are reaching `/api/llm-agent/...`
- the expected provider is being used
- tool failures, auth failures, or provider errors are visible and readable

## 12. After the staging test

If the staging test passes:

1. restore the notebook bootstrap to production URLs, unless you want to keep a
   staging-only notebook
2. publish the production wheel channel
3. rerun the same notebook smoke tests against the production wheel

## Quick checklist

- branch pushed to GitHub
- staging wheel published
- staging wheel URLs verified
- server started from the tested branch
- LLM backend enabled on the server
- provider config and API key verified
- fresh JupyterLite session opened
- notebook installs Caterva2 from `wheels-staging`
- session creation works
- prompts work
- reset/delete/new session works
- auth behavior is correct
- server logs look good
