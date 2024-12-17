import './main.scss'

// Import all of Bootstrap's JS
import * as bootstrap from 'bootstrap'

function activate(selector, trigger) {
    const url = new URL(document.URL);
    for (let el of document.querySelectorAll(selector)) {
        const href = new URL(el.href)
        if (url.pathname.startsWith(href.pathname)) {
            el.classList.add("active");
            el.setAttribute("hx-get", el.dataset.url);
        }
        else {
            el.classList.remove("active");
            el.setAttribute("hx-get", el.dataset.url + el.dataset.path);
        }
        htmx.process(el);
    }
}

function clearContent(selector) {
    document.querySelector(selector).innerHTML = "";
}

function _cleanMessage(resultElementID) {
    const msg = document.getElementById(resultElementID);
    if (msg) {
        msg.style.display = 'none';
        msg.replaceChildren();
    }

    return msg;
}

function displayMessage(message, resultElementID="result") {
    const msg = _cleanMessage(resultElementID);
    msg.appendChild(document.createTextNode(message));
    msg.style.display = 'block';
}

async function _submitForm(form, successURL, resultElementID, asJSON) {
    const errors = {
        LOGIN_BAD_CREDENTIALS:
            'Incorrect credentials, please verify the email address and password.',
        REGISTER_USER_ALREADY_EXISTS:
            'Email address already registered, did you forget your password?',
        RESET_PASSWORD_BAD_TOKEN:
            'Invalid or expired link, did you click an old or already used link?',
    };

    // Empty the result view
    const msg = _cleanMessage(resultElementID);

    // Send form
    const params = {};
    for (const field of form.elements) {
        if (field.name != "")
            params[field.name] = field.value;
    }

    const response = await fetch(
        form.action,
        {
            method: form.method,
            headers: {
                'Content-Type': (asJSON ? 'application/json' : 'application/x-www-form-urlencoded')
            },
            body: (asJSON ? JSON.stringify(params) : new URLSearchParams(params))
        },
    );

    if (response.ok) {
        // Success: redirect
        window.location.href = successURL;
    }
    else {
        // Error
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") != -1) {
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
            else if (typeof detail == 'object') {
                const error = detail.reason;
                msg.appendChild(document.createTextNode(error));
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
        }
        else {  // e.g. 500
            const error = await response.text();
            msg.appendChild(document.createTextNode(error));
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

function showAlert(content) {
    const container = document.querySelector("#alert-error");
    const template = document.querySelector("#alert-error-template");
    const clone = template.content.cloneNode(true);
    clone.querySelector("#alert-error-text").textContent = content;
    container.replaceChildren(clone);
}

function openTab(id) {
    if (id) {
        let el = document.querySelector(`${id}-tab`);
        let tab = new bootstrap.Tab(el);
        tab.show();
    }
}

window.activate = activate;
window.clearContent = clearContent;
window.openTab = openTab;
window.showAlert = showAlert;
window.submitForm = submitForm;
window.submitFormAsJSON = submitFormAsJSON;

export {bootstrap};
