const elements = {
  scene: document.querySelector("#envelope-scene"),
  envelope: document.querySelector("#open-envelope"),
  sparkles: document.querySelector("#envelope-sparkles"),
  invitation: document.querySelector("#invitation"),
  hostPanel: document.querySelector("#host-panel"),
  backToInvitation: document.querySelector("#back-to-invitation"),
  rsvpForm: document.querySelector("#rsvp-form"),
  rsvpMessage: document.querySelector("#rsvp-message"),
  plusOneOpen: document.querySelector("#plus-one-open"),
  plusOneDialog: document.querySelector("#plus-one-dialog"),
  plusOneFields: document.querySelector("#plus-one-fields"),
  plusOneMessage: document.querySelector("#plus-one-message"),
  plusOneAdd: document.querySelector("#plus-one-add"),
  plusOneSave: document.querySelector("#plus-one-save"),
  plusOneCancel: document.querySelector("#plus-one-cancel"),
  hostLogin: document.querySelector("#host-login"),
  hostPassword: document.querySelector('#host-login input[name="password"]'),
  hostPasswordToggle: document.querySelector("#toggle-host-password"),
  hostLoginMessage: document.querySelector("#host-login-message"),
  dashboard: document.querySelector("#dashboard"),
  pendingRsvpsTab: document.querySelector("#pending-rsvps-tab"),
  finalizedRsvpsTab: document.querySelector("#finalized-rsvps-tab"),
  rsvpCount: document.querySelector("#rsvp-count"),
  dashboardList: document.querySelector("#admin-rsvp-list"),
  dashboardMessage: document.querySelector("#dashboard-message"),
  toast: document.querySelector("#toast")
};

let wedding;
let toastTimer;
let plusOneNames = [];
let plusOneDraft = [];
let dashboardRsvps = [];
let showingFinalizedRsvps = false;
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function setMessage(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("is-error", isError);
}

function formatDate(value, options = { year: "numeric", month: "long", day: "numeric" }) {
  const date = new Date(`${value}T12:00:00`);
  return new Intl.DateTimeFormat("en-US", options).format(date);
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("is-visible"), 5200);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);
}

function splitContact(value) {
  const contact = String(value || "").trim();
  if (!contact) return { email: "", phone: "" };
  if (contact.includes("@")) return { email: contact, phone: "" };
  return { email: "", phone: contact };
}

function renderPlusOneFields() {
  elements.plusOneFields.replaceChildren();
  plusOneDraft.forEach((name, index) => {
    const row = document.createElement("div");
    row.className = "plus-one-field-row";

    const label = document.createElement("label");
    label.className = "field plus-one-field";
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 120;
    input.autocomplete = "off";
    input.value = name;
    input.placeholder = `Guest ${index + 1} full name`;
    input.setAttribute("aria-label", `Guest ${index + 1} full name`);
    label.append(input);

    const remove = document.createElement("button");
    remove.className = "text-button plus-one-remove";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove guest ${index + 1}`);
    remove.addEventListener("click", () => {
      plusOneDraft = [...elements.plusOneFields.querySelectorAll("input")].map((field) => field.value);
      plusOneDraft.splice(index, 1);
      renderPlusOneFields();
    });
    row.append(label, remove);
    elements.plusOneFields.append(row);
  });
  elements.plusOneAdd.disabled = plusOneDraft.length >= 4;
}

function openPlusOneDialog() {
  plusOneDraft = [...plusOneNames];
  if (!plusOneDraft.length) plusOneDraft.push("");
  renderPlusOneFields();
  setMessage(elements.plusOneMessage, "");
  elements.plusOneDialog.showModal();
  elements.plusOneFields.querySelector("input")?.focus();
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers }
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || "Something went wrong. Please try again.");
  return body;
}

function renderWedding(data) {
  const names = data.couple.displayName || `${data.couple.firstName} and ${data.couple.secondName}`;

  document.title = `${names} — Wedding Invitation`;
  document.querySelector('meta[name="description"]').content = data.message;
  document.querySelector('meta[name="theme-color"]').content = "#f4eadc";

  document.querySelector("#scene-note").textContent = data.welcome;
  if (data.rsvpBy) {
    document.querySelector("#rsvp-deadline").textContent = formatDate(data.rsvpBy);
  }
}

function openEnvelope() {
  if (elements.envelope.classList.contains("is-open")) return;
  elements.envelope.classList.add("is-open");
  elements.scene.classList.add("is-open");
  if (!prefersReducedMotion) {
    elements.envelope.classList.add("is-popping");
    elements.sparkles.classList.add("is-active");
  }
  const popDuration = prefersReducedMotion ? 0 : 1700;
  setTimeout(() => {
    elements.scene.classList.add("is-transitioning");
    setTimeout(() => {
      elements.scene.classList.add("hidden");
      elements.invitation.classList.remove("hidden");
      document.body.classList.add("showing-invitation");
      window.scrollTo({ top: 0, behavior: prefersReducedMotion ? "auto" : "smooth" });
    }, prefersReducedMotion ? 0 : 450);
  }, popDuration);
}

elements.envelope.addEventListener("click", openEnvelope);
elements.envelope.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openEnvelope();
  }
});

const attendanceInputs = [...document.querySelectorAll('.radio-card input[name="attendance"]')];
attendanceInputs.forEach((input) => {
  input.addEventListener("change", () => {
    attendanceInputs.forEach((option) => {
      option.closest(".radio-card").classList.toggle("is-selected", option.checked);
    });
  });
});

elements.rsvpForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (document.body.classList.contains("host-viewing-invitation")) return;
  const submit = document.querySelector("#rsvp-submit");
  const form = new FormData(elements.rsvpForm);
  const contact = splitContact(form.get("contact"));
  const attendance = form.get("attendance");
  const payload = {
    fullName: String(form.get("fullName") || "").trim(),
    email: contact.email,
    phone: contact.phone,
    attendance,
    dietaryNotes: String(form.get("dietaryNotes") || "").trim(),
    songRequest: String(form.get("songRequest") || "").trim(),
    plusOnes: attendance === "attending" ? plusOneNames : []
  };

  submit.disabled = true;
  setMessage(elements.rsvpMessage, "Sending your reply…");
  try {
    await requestJson("/api/rsvps", { method: "POST", body: JSON.stringify(payload) });
    elements.rsvpForm.reset();
    plusOneNames = [];
    elements.plusOneOpen.textContent = "Add guests";
    setMessage(
      elements.rsvpMessage,
      payload.plusOnes.length
        ? "Thank you! Your RSVP and named guest requests are with the host for review. The final guest list will be emailed after review."
        : "Thank you! Your RSVP has been received. We can’t wait to celebrate with you."
    );
  } catch (error) {
    setMessage(elements.rsvpMessage, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

elements.plusOneOpen.addEventListener("click", openPlusOneDialog);
elements.plusOneAdd.addEventListener("click", () => {
  if (plusOneDraft.length < 4) {
    plusOneDraft = [...elements.plusOneFields.querySelectorAll("input")].map((field) => field.value);
    plusOneDraft.push("");
    renderPlusOneFields();
    elements.plusOneFields.lastElementChild?.querySelector("input")?.focus();
  }
});
elements.plusOneCancel.addEventListener("click", () => elements.plusOneDialog.close());
elements.plusOneSave.addEventListener("click", () => {
  const inputs = [...elements.plusOneFields.querySelectorAll("input")];
  const names = inputs.map((input) => input.value.trim());
  if (names.some((name) => !name)) {
    setMessage(elements.plusOneMessage, "Enter a name for each guest or remove the empty field.", true);
    inputs.find((input) => !input.value.trim())?.focus();
    return;
  }
  plusOneNames = names;
  elements.plusOneOpen.textContent = names.length
    ? `${names.length} ${names.length === 1 ? "guest" : "guests"} added`
    : "Add guests";
  elements.plusOneDialog.close();
});

function showHostPanel() {
  elements.hostPassword.value = "";
  document.body.classList.remove("showing-invitation");
  document.body.classList.remove("host-viewing-invitation");
  elements.scene.classList.add("hidden");
  elements.invitation.classList.add("hidden");
  elements.hostPanel.classList.remove("hidden");
  elements.hostPanel.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth" });
  loadDashboard().catch(() => {});
}

function showInvitation() {
  elements.hostPanel.classList.add("hidden");
  elements.scene.classList.add("hidden");
  elements.invitation.classList.remove("hidden");
  document.body.classList.add("showing-invitation", "host-viewing-invitation");
  window.scrollTo({ top: 0, behavior: prefersReducedMotion ? "auto" : "smooth" });
}

elements.backToInvitation.addEventListener("click", showInvitation);

function renderRsvpCards(rsvps, emptyMessage) {
  if (!rsvps.length) {
    elements.dashboardList.innerHTML = `<p class="dashboard-message">${emptyMessage}</p>`;
    return;
  }

  const ordered = [...rsvps].sort(
    (first, second) => new Date(second.submittedAt) - new Date(first.submittedAt)
  );

  elements.dashboardList.innerHTML = ordered.map((rsvp) => {
    const attendance = rsvp.attendance === "attending" ? "Joyfully accepts" : "Regretfully declines";
    const contact = [rsvp.email, rsvp.phone].filter(Boolean).join(" · ");
    const plusOnes = Array.isArray(rsvp.plusOnes) ? rsvp.plusOnes : [];
    const finalized = rsvp.plusOneReviewFinalized === true;
    const companionReview = plusOnes.length ? `
      <form class="plus-one-review" data-rsvp-id="${escapeHtml(rsvp.id)}">
        <h4>Requested guests</h4>
        ${plusOnes.map((guest) => `
          <div class="plus-one-review-row">
            <span>${escapeHtml(guest.name)}</span>
            <div class="plus-one-review-actions">
              ${guest.status === "removed" ? '<span class="status-pill">Removed</span>' : `
                <select data-plus-one-id="${escapeHtml(guest.id)}" aria-label="Decision for ${escapeHtml(guest.name)}" ${finalized ? "disabled" : ""}>
                  <option value="pending" ${guest.status === "pending" ? "selected" : ""}>Choose</option>
                  <option value="approved" ${guest.status === "approved" ? "selected" : ""}>Approve</option>
                  <option value="declined" ${guest.status === "declined" ? "selected" : ""}>Decline</option>
                </select>`}
            </div>
          </div>`).join("")}
        <button class="button button-gold" type="submit" ${finalized ? "disabled" : ""}>
          ${finalized ? "Decisions finalized" : "Finalize guest list"}
        </button>
      </form>` : "";
    return `
      <article class="rsvp-card-item">
        <div class="rsvp-card-header">
          <div>
            <h3>${escapeHtml(rsvp.fullName)}</h3>
            <p>${escapeHtml(contact)} · ${attendance}</p>
          </div>
          <div class="rsvp-card-actions">
            <span class="status-pill">${escapeHtml(new Date(rsvp.submittedAt).toLocaleDateString())}</span>
            <button
              class="delete-rsvp-button"
              type="button"
              data-rsvp-id="${escapeHtml(rsvp.id)}"
              data-guest-name="${escapeHtml(rsvp.fullName)}"
              aria-label="Delete RSVP for ${escapeHtml(rsvp.fullName)}"
            >Delete RSVP</button>
          </div>
        </div>
        ${rsvp.dietaryNotes ? `<p><strong>Dietary:</strong> ${escapeHtml(rsvp.dietaryNotes)}</p>` : ""}
        ${rsvp.songRequest ? `<p><strong>Song request:</strong> ${escapeHtml(rsvp.songRequest)}</p>` : ""}
        ${companionReview}
      </article>`;
  }).join("");
}

function renderRsvps(rsvps) {
  dashboardRsvps = rsvps;
  const pending = rsvps.filter((rsvp) => rsvp.plusOneReviewFinalized !== true);
  const finalized = rsvps.filter((rsvp) => rsvp.plusOneReviewFinalized === true);
  elements.rsvpCount.textContent =
    `${pending.length} ${pending.length === 1 ? "request to review" : "requests to review"}`;
  elements.pendingRsvpsTab.textContent = `Needs review (${pending.length})`;
  elements.finalizedRsvpsTab.textContent = `Finalized (${finalized.length})`;
  elements.pendingRsvpsTab.classList.toggle("is-active", !showingFinalizedRsvps);
  elements.pendingRsvpsTab.setAttribute("aria-pressed", String(!showingFinalizedRsvps));
  elements.finalizedRsvpsTab.classList.toggle("is-active", showingFinalizedRsvps);
  elements.finalizedRsvpsTab.setAttribute("aria-pressed", String(showingFinalizedRsvps));

  renderRsvpCards(
    showingFinalizedRsvps ? finalized : pending,
    showingFinalizedRsvps ? "No requests have been finalized yet." : "No requests are waiting for review."
  );
}

elements.pendingRsvpsTab.addEventListener("click", () => {
  showingFinalizedRsvps = false;
  renderRsvps(dashboardRsvps);
});

elements.finalizedRsvpsTab.addEventListener("click", () => {
  showingFinalizedRsvps = true;
  renderRsvps(dashboardRsvps);
});

elements.dashboardList.addEventListener("click", async (event) => {
  const deleteButton = event.target.closest(".delete-rsvp-button");
  if (!deleteButton) return;
  const guestName = deleteButton.dataset.guestName || "this guest";
  if (!window.confirm(`Permanently delete the RSVP for ${guestName}, including all requested guests?`)) {
    return;
  }

  deleteButton.disabled = true;
  setMessage(elements.dashboardMessage, "Deleting the RSVP…");
  try {
    await requestJson(`/api/admin/rsvps/${encodeURIComponent(deleteButton.dataset.rsvpId)}`, {
      method: "DELETE"
    });
    await loadDashboard();
    setMessage(elements.dashboardMessage, `The RSVP for ${guestName} was deleted.`);
  } catch (error) {
    setMessage(elements.dashboardMessage, error.message, true);
    deleteButton.disabled = false;
  }
});

elements.dashboardList.addEventListener("submit", async (event) => {
  const reviewForm = event.target.closest(".plus-one-review");
  if (!reviewForm) return;
  event.preventDefault();
  const selections = [...reviewForm.querySelectorAll("select[data-plus-one-id]")];
  if (selections.some((selection) => selection.value === "pending")) {
    setMessage(elements.dashboardMessage, "Choose approve or decline for every requested guest.", true);
    return;
  }

  const submit = reviewForm.querySelector("button[type='submit']");
  submit.disabled = true;
  setMessage(elements.dashboardMessage, "Saving the final guest list…");
  try {
    const result = await requestJson(
      `/api/admin/rsvps/${encodeURIComponent(reviewForm.dataset.rsvpId)}/plus-ones`,
      {
        method: "PATCH",
        body: JSON.stringify({
          decisions: selections.map((selection) => ({
            id: selection.dataset.plusOneId,
            decision: selection.value
          }))
        })
      }
    );
    await loadDashboard();
    setMessage(
      elements.dashboardMessage,
      result.notification.sent
        ? "Final decisions saved and emailed to the guest."
        : `Final decisions saved. ${result.notification.reason}`
    );
  } catch (error) {
    setMessage(elements.dashboardMessage, error.message, true);
    submit.disabled = false;
  }
});

async function loadDashboard() {
  elements.dashboardList.innerHTML = "";
  setMessage(elements.dashboardMessage, "Loading your guest list…");
  try {
    const result = await requestJson("/api/admin/rsvps");
    elements.hostLogin.classList.add("hidden");
    elements.dashboard.classList.remove("hidden");
    renderRsvps(result.rsvps);
    setMessage(elements.dashboardMessage, "");
  } catch (error) {
    elements.dashboard.classList.add("hidden");
    elements.hostLogin.classList.remove("hidden");
    if (!/sign in/i.test(error.message)) {
      setMessage(elements.hostLoginMessage, error.message, true);
    } else {
      setMessage(elements.hostLoginMessage, "");
    }
  }
}

elements.hostPasswordToggle.addEventListener("click", () => {
  const isVisible = elements.hostPassword.type === "text";
  elements.hostPassword.type = isVisible ? "password" : "text";
  elements.hostPasswordToggle.classList.toggle("is-visible", !isVisible);
  elements.hostPasswordToggle.setAttribute("aria-pressed", String(!isVisible));
  elements.hostPasswordToggle.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
  elements.hostPasswordToggle.title = isVisible ? "Show password" : "Hide password";
});

elements.hostLogin.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = elements.hostLogin.querySelector("button");
  submit.disabled = true;
  setMessage(elements.hostLoginMessage, "Signing in…");
  try {
    const password = new FormData(elements.hostLogin).get("password");
    await requestJson("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password })
    });
    elements.hostLogin.reset();
    await loadDashboard();
  } catch (error) {
    elements.hostPassword.value = "";
    setMessage(elements.hostLoginMessage, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector("#host-logout").addEventListener("click", async () => {
  try {
    await requestJson("/api/admin/logout", { method: "POST", body: "{}" });
    elements.dashboard.classList.add("hidden");
    elements.hostLogin.classList.remove("hidden");
    setMessage(elements.hostLoginMessage, "You’ve signed out.");
  } catch (error) {
    setMessage(elements.dashboardMessage, error.message, true);
  }
});

async function start() {
  try {
    const response = await fetch("/api/wedding");
    if (!response.ok) throw new Error("The wedding details could not be loaded.");
    wedding = await response.json();
    renderWedding(wedding);
    if (window.location.pathname === "/host") showHostPanel();
  } catch (error) {
    showToast(error.message);
  }
}

start();
