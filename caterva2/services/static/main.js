function activate(selector, trigger) {
    const url = new URL(document.URL);
    for (el of document.querySelectorAll(selector)) {
        if (trigger === undefined) {
            const href = new URL(el.href)
            if (url.pathname.startsWith(href.pathname)) {
                el.classList.add("active");
            }
            else {
                el.classList.remove("active");
            }
        }
        else {
            if (trigger == el) {
                el.classList.add("active");
            }
            else {
                el.classList.remove("active");
            }
        }
    }
}

function clearContent(selector) {
    document.querySelector(selector).innerHTML = "";
}

async function _submitForm(form, successURL, resultElementID, asJSON) {
    const errors = {
        LOGIN_BAD_CREDENTIALS:
            'Incorrect credentials, please verify the email address and password.',
        REGISTER_USER_ALREADY_EXISTS:
            'Email address already registered, did you forgot your password?',
    };

    // Empty the result view
    const msg = document.getElementById(resultElementID);
    if (msg) {
        msg.style.display = 'none';
        msg.replaceChildren();
    }

    // Send form
    const params = {};
    for (const field of form.elements)
        if (field.name != "")
            params[field.name] = field.value;

    const response = await fetch(
        form.action, {
            method: form.method,
            headers: {'Content-Type': (asJSON ? 'application/json'
                                       : 'application/x-www-form-urlencoded')},
            body: (asJSON ? JSON.stringify(params)
                   : new URLSearchParams(params))},
    );

    if (response.ok) {
        // Success: redirect
        window.location.href = successURL;
    }
    else {
        // Error
        const json = await response.json();
        const detail = json.detail;
        if (Array.isArray(detail)) {
            // TODO Improve error display
            for (let error of detail) {
                msg.appendChild(document.createTextNode(
                    `${error.msg}, `
                ));
            }
        }
        else if (detail) {
            const error = errors[detail] || detail;
            msg.appendChild(document.createTextNode(error));
        }
        else {
            msg.appendChild(document.createTextNode(
                `Unexpected error: ${response.status} ${response.statusText}`
            ));
            msg.appendChild(document.createElement("pre"))
               .textContent = JSON.stringify(json);
        }

        msg.style.display = 'block';
    }
}

async function submitForm(form, successURL, resultElementID="result") {
    return await _submitForm(form, successURL, resultElementID, false);
}

async function submitFormAsJSON(form, successURL, resultElementID="result") {
    return await _submitForm(form, successURL, resultElementID, true);
}
