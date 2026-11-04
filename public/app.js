const elements = {
  scene: document.querySelector("#envelope-scene"),
  envelope: document.querySelector("#open-envelope"),
  sparkles: document.querySelector("#envelope-sparkles"),
  invitation: document.querySelector("#invitation"),
  hostPanel: document.querySelector("#host-panel"),
  backToInvitation: document.querySelector("#back-to-invitation"),
  rsvpForm: document.querySelector("#rsvp-form"),
  rsvpMessage: document.querySelector("#rsvp-message"),
  fullName: document.querySelector("#full-name"),
  inviteAccessMessage: document.querySelector("#invite-access-message"),
  plusOneOpen: document.querySelector("#plus-one-open"),
  plusOneDialog: document.querySelector("#plus-one-dialog"),
  plusOneFields: document.querySelector("#plus-one-fields"),
  plusOneMessage: document.querySelector("#plus-one-message"),
  plusOneAdd: document.querySelector("#plus-one-add"),
  plusOneSave: document.querySelector("#plus-one-save"),
  plusOneCancel: document.querySelector("#plus-one-cancel"),
  plusOneLimit: document.querySelector("#plus-one-limit"),
  hostLogin: document.querySelector("#host-login"),
  hostPassword: document.querySelector('#host-login input[name="password"]'),
  hostPasswordToggle: document.querySelector("#toggle-host-password"),
  hostLoginMessage: document.querySelector("#host-login-message"),
  dashboard: document.querySelector("#dashboard"),
  pendingRsvpsTab: document.querySelector("#pending-rsvps-tab"),
  finalizedRsvpsTab: document.querySelector("#finalized-rsvps-tab"),
  declinedRsvpsTab: document.querySelector("#declined-rsvps-tab"),
  rsvpCount: document.querySelector("#rsvp-count"),
  dashboardList: document.querySelector("#admin-rsvp-list"),
  dashboardMessage: document.querySelector("#dashboard-message"),
  invitationAddForm: document.querySelector("#invitation-add-form"),
  invitationManagementMessage: document.querySelector("#invitation-management-message"),
  invitationList: document.querySelector("#invitation-list"),
  toast: document.querySelector("#toast")
};

let wedding;
let toastTimer;
let plusOneNames = [];
let plusOneDraft = [];
let dashboardRsvps = [];
let dashboardInvitations = [];
let selectedInvitationId = "";
let activeRsvpTab = "pending";
let invitationLookupId = 0;
let invitationLookupTimer;
let inviteEligibility = { nameKey: "", invited: false, allowedPlusOnes: 0 };
const MAX_PLUS_ONES = 20;
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
  elements.plusOneAdd.disabled = plusOneDraft.length >= inviteEligibility.allowedPlusOnes;
}

function openPlusOneDialog() {
  if (!inviteEligibility.invited || inviteEligibility.allowedPlusOnes === 0) return;
  plusOneDraft = [...plusOneNames];
  if (!plusOneDraft.length) plusOneDraft.push("");
  renderPlusOneFields();
  setMessage(elements.plusOneMessage, "");
  elements.plusOneDialog.showModal();
  elements.plusOneFields.querySelector("input")?.focus();
}

function updatePlusOneButton() {
  const count = plusOneNames.length;
  const currentNameKey = elements.fullName.value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  const invitationMatchesInput = inviteEligibility.invited && inviteEligibility.nameKey === currentNameKey;
  elements.plusOneOpen.textContent = count
    ? `${count} ${count === 1 ? "guest" : "guests"} added`
    : "Add guests";
  const canAddGuests = invitationMatchesInput && inviteEligibility.allowedPlusOnes > 0;
  elements.plusOneOpen.classList.toggle("hidden", !canAddGuests);
  elements.plusOneOpen.disabled = !canAddGuests;
  document.querySelector("#rsvp-submit").disabled = !invitationMatchesInput;
}

function resetInviteEligibility() {
  clearTimeout(invitationLookupTimer);
  invitationLookupId += 1;
  inviteEligibility = { nameKey: "", invited: false, allowedPlusOnes: 0 };
  plusOneNames = [];
  updatePlusOneButton();
  elements.plusOneLimit.textContent = "Your invitation determines how many additional guests you may request.";
  setMessage(elements.inviteAccessMessage, "");
}

async function checkInvitation(name) {
  clearTimeout(invitationLookupTimer);
  const cleanedName = name.trim().replace(/\s+/g, " ");
  const nameKey = cleanedName.toLocaleLowerCase();
  const requestId = ++invitationLookupId;
  if (!cleanedName) {
    resetInviteEligibility();
    return;
  }

  setMessage(elements.inviteAccessMessage, "Checking your invitation…");
  try {
    const result = await requestJson("/api/invitations/lookup", {
      method: "POST",
      body: JSON.stringify({ name: cleanedName })
    });
    if (requestId !== invitationLookupId) return;
    inviteEligibility = {
      nameKey,
      invited: result.invited,
      allowedPlusOnes: result.allowedPlusOnes
    };
    if (!result.invited) {
      elements.plusOneLimit.textContent = "Only guests on the host's invitation list may request additional guests.";
      setMessage(
        elements.inviteAccessMessage,
        "We couldn't find an invitation for this name. Only invited guests can RSVP. Please check the name or contact the hosts."
      );
    } else if (result.allowedPlusOnes === 0) {
      elements.plusOneLimit.textContent = "Your invitation does not include additional guests.";
      setMessage(elements.inviteAccessMessage, "Your invitation allows no additional guests.");
    } else {
      const guestLabel = result.allowedPlusOnes === 1 ? "guest" : "guests";
      elements.plusOneLimit.textContent = `You may request up to ${result.allowedPlusOnes} additional ${guestLabel}.`;
      setMessage(elements.inviteAccessMessage, `Your invitation allows up to ${result.allowedPlusOnes} additional ${guestLabel}.`);
    }
    updatePlusOneButton();
  } catch (error) {
    if (requestId !== invitationLookupId) return;
    inviteEligibility = { nameKey, invited: false, allowedPlusOnes: 0 };
    updatePlusOneButton();
    setMessage(elements.inviteAccessMessage, error.message, true);
  }
}

updatePlusOneButton();

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
  elements.envelope.classList.add("is-popping");
  elements.sparkles.classList.add("is-active");
  const popDuration = prefersReducedMotion ? 550 : 1700;
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

elements.fullName.addEventListener("input", () => {
  const nameKey = elements.fullName.value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  if (nameKey !== inviteEligibility.nameKey) {
    resetInviteEligibility();
    if (nameKey) {
      invitationLookupTimer = setTimeout(() => checkInvitation(elements.fullName.value), 400);
    }
  }
});
elements.fullName.addEventListener("blur", () => {
  checkInvitation(elements.fullName.value);
});

elements.rsvpForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (document.body.classList.contains("host-viewing-invitation")) return;
  const submit = document.querySelector("#rsvp-submit");
  await checkInvitation(elements.fullName.value);
  const currentNameKey = elements.fullName.value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  if (!inviteEligibility.invited || inviteEligibility.nameKey !== currentNameKey) {
    setMessage(
      elements.rsvpMessage,
      "We couldn't find an invitation for this name. Only invited guests can RSVP. Please check the name or contact the hosts.",
      true
    );
    updatePlusOneButton();
    return;
  }
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
    resetInviteEligibility();
    setMessage(
      elements.rsvpMessage,
      payload.plusOnes.length
        ? "Thank you! Your RSVP and named guest requests are with the host for review. The final guest list will be emailed after review."
        : "Thank you! Your RSVP has been received. We can’t wait to celebrate with you."
    );
  } catch (error) {
    setMessage(elements.rsvpMessage, error.message, true);
  } finally {
    updatePlusOneButton();
  }
});

elements.plusOneOpen.addEventListener("click", openPlusOneDialog);
elements.plusOneAdd.addEventListener("click", () => {
  if (plusOneDraft.length < inviteEligibility.allowedPlusOnes) {
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
  if (names.length > inviteEligibility.allowedPlusOnes) {
    setMessage(
      elements.plusOneMessage,
      `Your invitation allows up to ${inviteEligibility.allowedPlusOnes} additional guests.`,
      true
    );
    return;
  }
  if (names.some((name) => !name)) {
    setMessage(elements.plusOneMessage, "Enter a name for each guest or remove the empty field.", true);
    inputs.find((input) => !input.value.trim())?.focus();
    return;
  }
  plusOneNames = names;
  updatePlusOneButton();
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
  const pending = rsvps.filter((rsvp) => {
    const plusOnes = Array.isArray(rsvp.plusOnes) ? rsvp.plusOnes : [];
    return rsvp.attendance !== "declining"
      && plusOnes.length > 0
      && rsvp.plusOneReviewFinalized !== true;
  });
  const finalized = rsvps.filter((rsvp) =>
    rsvp.attendance !== "declining"
    && (rsvp.plusOneReviewFinalized === true || !Array.isArray(rsvp.plusOnes) || rsvp.plusOnes.length === 0)
  );
  const declined = rsvps.filter((rsvp) => rsvp.attendance === "declining");
  elements.rsvpCount.textContent =
    `${pending.length} ${pending.length === 1 ? "request to review" : "requests to review"}`;
  elements.pendingRsvpsTab.textContent = `Needs review (${pending.length})`;
  elements.finalizedRsvpsTab.textContent = `Finalized (${finalized.length})`;
  elements.declinedRsvpsTab.textContent = `Can't attend (${declined.length})`;
  [
    ["pending", elements.pendingRsvpsTab],
    ["finalized", elements.finalizedRsvpsTab],
    ["declined", elements.declinedRsvpsTab]
  ].forEach(([tabName, tab]) => {
    const isActive = activeRsvpTab === tabName;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-pressed", String(isActive));
  });

  const tabs = {
    pending: [pending, "No requests are waiting for review."],
    finalized: [finalized, "No requests have been finalized yet."],
    declined: [declined, "No guests have declined the invitation."]
  };
  const [visibleRsvps, emptyMessage] = tabs[activeRsvpTab];
  renderRsvpCards(visibleRsvps, emptyMessage);
}

function renderInvitations(invitations) {
  dashboardInvitations = invitations;
  if (!invitations.length) {
    elements.invitationList.innerHTML = '<p class="invitation-list-empty">No invitations yet. Add the first invitee above.</p>';
    return;
  }

  if (!invitations.some((invitation) => invitation.id === selectedInvitationId)) {
    selectedInvitationId = "";
  }
  const selectedInvitation = invitations.find((invitation) => invitation.id === selectedInvitationId);
  const details = selectedInvitation ? `
    <article class="invitation-item" data-invitation-id="${escapeHtml(selectedInvitation.id)}">
      <div class="invitation-item-summary">
        <div>
          <h3>${escapeHtml(selectedInvitation.name)}</h3>
          <p>Allowed plus-ones: ${selectedInvitation.allowedPlusOnes} · ${
            selectedInvitation.rsvpStatus === "attending"
              ? "Joyfully accepts"
              : selectedInvitation.rsvpStatus === "declining"
                ? "Regretfully declines"
                : "No RSVP yet"
          } · ${selectedInvitation.requestedPlusOnes} ${
            selectedInvitation.requestedPlusOnes === 1 ? "guest" : "guests"
          } requested</p>
        </div>
        <div class="invitation-item-actions">
          <button class="invitation-edit-button" type="button">Edit details</button>
          <button class="invitation-remove-button" type="button">Remove invite</button>
        </div>
      </div>
      <form class="invitation-edit-form hidden">
        <label class="field">
          <span>Invitee name</span>
          <input name="name" maxlength="120" value="${escapeHtml(selectedInvitation.name)}" required>
        </label>
        <label class="field">
          <span>Allowed plus-ones</span>
          <input name="allowedPlusOnes" type="number" min="0" max="20" value="${selectedInvitation.allowedPlusOnes}" required>
        </label>
        <div class="invitation-edit-actions">
          <button class="invitation-edit-cancel" type="button">Cancel</button>
          <button class="invitation-edit-save" type="submit">Save changes</button>
        </div>
      </form>
    </article>` : "";
  elements.invitationList.innerHTML = `
    <div class="invitation-select-field">
      <label for="invitation-select"><span>Select an invitee</span></label>
      <div class="invitation-select-controls">
        <select id="invitation-select">
          <option value="" disabled hidden ${selectedInvitationId ? "" : "selected"}>Choose a guest</option>
          ${invitations.map((invitation) => `
            <option value="${escapeHtml(invitation.id)}" ${invitation.id === selectedInvitationId ? "selected" : ""}>
              ${escapeHtml(invitation.name)}
            </option>`).join("")}
        </select>
        ${selectedInvitationId ? '<button class="invitation-clear-selection" type="button">Clear</button>' : ""}
      </div>
    </div>
    ${details}`;
}

elements.invitationList.addEventListener("change", (event) => {
  if (event.target.id !== "invitation-select") return;
  selectedInvitationId = event.target.value;
  renderInvitations(dashboardInvitations);
});

elements.invitationList.addEventListener("click", (event) => {
  if (!event.target.closest(".invitation-clear-selection")) return;
  selectedInvitationId = "";
  renderInvitations(dashboardInvitations);
});

elements.invitationAddForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.invitationAddForm);
  const submit = elements.invitationAddForm.querySelector("button[type='submit']");
  submit.disabled = true;
  setMessage(elements.invitationManagementMessage, "Adding invitation…");
  try {
    const result = await requestJson("/api/admin/invitations", {
      method: "POST",
      body: JSON.stringify({
        name: String(form.get("name") || "").trim(),
        allowedPlusOnes: Number(form.get("allowedPlusOnes"))
      })
    });
    selectedInvitationId = result.invitation.id;
    elements.invitationAddForm.reset();
    elements.invitationAddForm.elements.allowedPlusOnes.value = "0";
    await loadDashboard();
    setMessage(elements.invitationManagementMessage, "Invitation added.");
  } catch (error) {
    setMessage(elements.invitationManagementMessage, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

elements.invitationList.addEventListener("click", async (event) => {
  const editButton = event.target.closest(".invitation-edit-button");
  const cancelButton = event.target.closest(".invitation-edit-cancel");
  if (editButton || cancelButton) {
    const item = event.target.closest(".invitation-item");
    const editForm = item.querySelector(".invitation-edit-form");
    editForm.classList.toggle("hidden", Boolean(cancelButton));
    if (editButton) editForm.elements.name.focus();
    return;
  }

  const removeButton = event.target.closest(".invitation-remove-button");
  if (!removeButton) return;
  const item = removeButton.closest(".invitation-item");
  const invitation = dashboardInvitations.find((entry) => entry.id === item.dataset.invitationId);
  if (!invitation || !window.confirm(`Remove the invitation for ${invitation.name}?`)) return;

  removeButton.disabled = true;
  setMessage(elements.invitationManagementMessage, "Removing invitation…");
  try {
    await requestJson(`/api/admin/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE" });
    selectedInvitationId = "";
    await loadDashboard();
    setMessage(elements.invitationManagementMessage, `Invitation for ${invitation.name} removed.`);
  } catch (error) {
    setMessage(elements.invitationManagementMessage, error.message, true);
    removeButton.disabled = false;
  }
});

elements.invitationList.addEventListener("submit", async (event) => {
  const editForm = event.target.closest(".invitation-edit-form");
  if (!editForm) return;
  event.preventDefault();
  const item = editForm.closest(".invitation-item");
  const invitation = dashboardInvitations.find((entry) => entry.id === item.dataset.invitationId);
  if (!invitation) return;

  const form = new FormData(editForm);
  const submit = editForm.querySelector("button[type='submit']");
  submit.disabled = true;
  setMessage(elements.invitationManagementMessage, "Saving invitation…");
  try {
    await requestJson(`/api/admin/invitations/${encodeURIComponent(invitation.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: String(form.get("name") || "").trim(),
        allowedPlusOnes: Number(form.get("allowedPlusOnes"))
      })
    });
    await loadDashboard();
    setMessage(elements.invitationManagementMessage, "Invitation updated.");
  } catch (error) {
    setMessage(elements.invitationManagementMessage, error.message, true);
    submit.disabled = false;
  }
});

elements.pendingRsvpsTab.addEventListener("click", () => {
  activeRsvpTab = "pending";
  renderRsvps(dashboardRsvps);
});

elements.finalizedRsvpsTab.addEventListener("click", () => {
  activeRsvpTab = "finalized";
  renderRsvps(dashboardRsvps);
});

elements.declinedRsvpsTab.addEventListener("click", () => {
  activeRsvpTab = "declined";
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
  elements.invitationList.innerHTML = "";
  setMessage(elements.dashboardMessage, "Loading your guest list…");
  try {
    const [result, invitationResult] = await Promise.all([
      requestJson("/api/admin/rsvps"),
      requestJson("/api/admin/invitations")
    ]);
    elements.hostLogin.classList.add("hidden");
    elements.dashboard.classList.remove("hidden");
    renderRsvps(result.rsvps);
    renderInvitations(invitationResult.invitations);
    setMessage(elements.dashboardMessage, "");
    setMessage(elements.invitationManagementMessage, "");
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
