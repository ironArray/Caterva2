import './main.scss'
import './pygments.css'

// Import all of Bootstrap's JS
import * as bootstrap from 'bootstrap'

function activate(selector) {
    const url = new URL(document.URL);
    for (let el of document.querySelectorAll(selector)) {
        const href = new URL(el.href)
        if (url.pathname.startsWith(href.pathname)) {
            el.classList.add("active");
        }
        else {
            el.classList.remove("active");
        }
    }
}

function loadDataset(event) {
    event.preventDefault();
    const link = event.currentTarget;

    // Determine the URL to request:
    let url;
    if (link.classList.contains('active')) {
        // De-select: request base URL WITHOUT path (clears metadata)
        url = link.dataset.url;
    } else {
        // Select: request full URL WITH path
        url = link.dataset.url + encodeURIComponent(link.dataset.path);
    }

    // Trigger request and update active state afterwards
    htmx.ajax('GET', url, {
        target: '#meta',
        indicator: '#meta-wrapper .htmx-indicator'
    }).then(() => {
        // After request, update active state based on what was clicked
        if (link.classList.contains('active')) {
            // We just de-selected: remove active from all
            activate('#path-list a');
        } else {
            // We just selected: activate this one
            activate('#path-list a', link);
        }
    }).catch(() => {
        // On error, still sync the UI to reflect user intent
        if (link.classList.contains('active')) {
            activate('#path-list a');
        } else {
            activate('#path-list a', link);
        }
    });
}

// Expose to global scope for HTMX
window.loadDataset = loadDataset;

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

function disable(ev) {
    ev.preventDefault();
}

function handleSubmit(evt, form) {
    let btn = form.querySelector('button');
    btn.onclick = function(ev) {
        ev.preventDefault();
        htmx.trigger(evt.detail.elt, 'htmx:abort');
    };
    btn.innerHTML_bak = btn.innerHTML;
    btn.innerHTML = 'Abort <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
}

function resetForm(ev, form) {
    if (ev.detail.xhr.status < 400) {
        form.reset();
    }

    let btn = form.querySelector('button');
    btn.innerHTML = btn.innerHTML_bak;
    btn.onclick = null;
}

window.activate = activate;
window.clearContent = clearContent;
window.openTab = openTab;
window.showAlert = showAlert;
window.submitForm = submitForm;
window.submitFormAsJSON = submitFormAsJSON;

window.handleSubmit = handleSubmit;
window.resetForm = resetForm;

export {bootstrap};
