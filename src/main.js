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

function _updateURL(src, options) {
    const url = new URL(src);
    for (const [key, value] of Object.entries(options)) {
        url.searchParams.set(key, value);
    }
    return url.toString();
}

function _updateImage(options) {
    let img = document.getElementById('display-image');
    document.getElementById('image-spinner').classList.add('htmx-request');
    img.src = _updateURL(img.src, options);
}

function update_i(input) {
    const options = {i: input.value};
    _updateImage(options);

    let link = document.getElementById('image-original');
    if (link) {
        link.href = _updateURL(link.href, options);
    }
}

function update_ndim(select) {
    // Update image URL
    const option = select.selectedOptions[0];
    const options = {ndim: option.value, i: 0};
    _updateImage(options);

    // Update max of input element
    const size = option.getAttribute('data-size');
    const input = document.querySelector('input[name="i"]');
    input.setAttribute('max', size - 1);
    input.value = 0;

    // Update link to original size image
    let link = document.getElementById('image-original');
    if (link) {
        link.href = _updateURL(link.href, options);
        const [h, w] = [...select.options].filter(opt => !opt.selected).map(opt => opt.dataset.size);
        link.textContent = `${w} x ${h} (original size)`;
    }
}

function stopSpinner() {
    const spinner = document.getElementById('image-spinner');
    if (spinner) {
        spinner.classList.remove('htmx-request');
    }
}


// Wheel over the data table pages the dim-0 window via its htmx-wired input
let wheelBusy = false;
document.addEventListener('wheel', (ev) => {
    const table = ev.target.closest('#info-view table');
    if (!table) return;
    const input = document.querySelector('#info-view-form input[name="index"]:not([readonly])');
    if (!input) return;              // whole dim fits: nothing to page
    ev.preventDefault();             // don't also scroll the page
    if (wheelBusy) return;           // one page per gesture / in-flight request
    wheelBusy = true;
    setTimeout(() => { wheelBusy = false; }, 300);
    const before = input.value;
    ev.deltaY > 0 ? input.stepUp() : input.stepDown();  // step == window size, clamped to min/max
    if (input.value !== before) htmx.trigger(input, 'change');
}, { passive: false });

// Drag the lateral position bar in the Display tab to seek within the dataset.
// Pointer events (not mouse events) so this also works with touch/pen.
document.addEventListener('pointerdown', (ev) => {
    const bar = ev.target.closest('.info-view-scrollbar');
    if (!bar) return;
    const input = document.querySelector('#info-view-form input[name="index"]:not([readonly])');
    if (!input) return;
    ev.preventDefault();  // no text-selection drag artifacts
    bar.setPointerCapture(ev.pointerId);  // keep tracking even if pointer leaves the bar
    bar.classList.add('dragging');
    const max = Number(input.max), step = Number(input.step);
    const sizeMax = max + step;
    const seek = (clientY) => {
        const rect = bar.getBoundingClientRect();
        const frac = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
        const start = Math.min(max, Math.round(frac * sizeMax / step) * step);
        if (String(start) !== input.value) {
            input.value = start;
            htmx.trigger(input, 'change');
        }
    };
    seek(ev.clientY);
    const onMove = (e) => seek(e.clientY);
    const onUp = () => {
        bar.classList.remove('dragging');
        bar.removeEventListener('pointermove', onMove);
        bar.removeEventListener('pointerup', onUp);
        bar.removeEventListener('pointercancel', onUp);
    };
    bar.addEventListener('pointermove', onMove);
    bar.addEventListener('pointerup', onUp);
    bar.addEventListener('pointercancel', onUp);
});

window.activate = activate;
window.clearContent = clearContent;
window.openTab = openTab;
window.showAlert = showAlert;
window.submitForm = submitForm;
window.submitFormAsJSON = submitFormAsJSON;

window.handleSubmit = handleSubmit;
window.resetForm = resetForm;

window.update_i = update_i;
window.update_ndim = update_ndim;
window.stopSpinner = stopSpinner;

export {bootstrap};
