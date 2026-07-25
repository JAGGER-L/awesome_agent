const r4Selections = { icon: "I2", mark: "M1", toc: "P4", home: "H1" };

function r4SelectionText(separator = " · ") {
  return [r4Selections.icon, r4Selections.mark, r4Selections.toc, r4Selections.home].join(separator);
}

const r4Output = document.querySelector("#r4-selection");
const r4Copy = document.querySelector("#r4-copy");

document.querySelectorAll("[data-r4-card]").forEach((card) => {
  const choose = card.querySelector("[data-r4-choose]");
  if (!choose) return;
  choose.setAttribute("aria-pressed", card.classList.contains("is-selected") ? "true" : "false");
  choose.addEventListener("click", () => {
    const group = card.dataset.group;
    r4Selections[group] = card.dataset.value;
    document.querySelectorAll(`[data-r4-card][data-group="${group}"]`).forEach((peer) => {
      const active = peer === card;
      peer.classList.toggle("is-selected", active);
      const button = peer.querySelector("[data-r4-choose]");
      if (!button) return;
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.textContent = active ? "已选择" : `选择 ${peer.dataset.value}`;
    });
    if (r4Output) r4Output.textContent = r4SelectionText();
  });
});

if (r4Copy) {
  r4Copy.addEventListener("click", async () => {
    const value = r4SelectionText(" + ");
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const field = document.createElement("textarea");
      field.value = value;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.append(field);
      field.select();
      document.execCommand("copy");
      field.remove();
    }
    r4Copy.textContent = "已复制";
    window.setTimeout(() => { r4Copy.textContent = "复制组合"; }, 1300);
  });
}

document.querySelectorAll("[data-sample-theme-toggle]").forEach((button) => {
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => {
    const sample = button.closest("[data-theme-sample]");
    const dark = sample.classList.toggle("is-dark");
    button.classList.toggle("is-dark", dark);
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    const label = button.querySelector("[data-theme-state-label]");
    if (label) label.textContent = dark ? "Dark" : "Light";
  });
});

document.querySelectorAll(".new-toc-demo nav").forEach((toc) => {
  const links = [...toc.querySelectorAll("[data-new-toc-link]")];
  links.forEach((link, index) => {
    link.setAttribute("aria-current", index === 0 ? "location" : "false");
    link.addEventListener("click", () => {
      links.forEach((peer) => {
        const active = peer === link;
        peer.classList.toggle("active", active);
        peer.setAttribute("aria-current", active ? "location" : "false");
      });
      const count = toc.querySelector("[data-reading-count]");
      const bar = toc.querySelector("[data-reading-bar]");
      if (count) count.textContent = `${index + 1} of ${links.length}`;
      if (bar) bar.style.width = `${((index + 1) / links.length) * 100}%`;
      const current = toc.querySelector("[data-current-section]");
      if (current) current.textContent = link.textContent.trim();
    });
  });
});

document.querySelectorAll("[data-compact-toggle]").forEach((trigger) => {
  trigger.addEventListener("click", () => {
    const menu = trigger.parentElement.querySelector("[data-compact-menu]");
    const open = menu.hidden;
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
  });
});

document.querySelectorAll("[data-global-theme-toggle]").forEach((button) => {
  button.setAttribute("aria-pressed", document.body.classList.contains("is-dark") ? "true" : "false");
  button.addEventListener("click", () => {
    const dark = document.body.classList.toggle("is-dark");
    document.querySelectorAll("[data-global-theme-toggle]").forEach((peer) => {
      peer.classList.toggle("is-dark", dark);
      peer.setAttribute("aria-pressed", dark ? "true" : "false");
    });
  });
});

function setGlobalLanguage(language) {
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.querySelectorAll(".global-language").forEach((switcher) => {
    switcher.classList.toggle("is-en", language === "en");
    switcher.querySelectorAll("[data-global-language-choice]").forEach((choice) => {
      choice.classList.toggle("active", choice.dataset.globalLanguageChoice === language);
    });
  });
  document.querySelectorAll("[data-copy-zh][data-copy-en]").forEach((node) => {
    node.textContent = language === "zh" ? node.dataset.copyZh : node.dataset.copyEn;
  });
}

document.querySelectorAll("[data-global-language-choice]").forEach((choice) => {
  choice.addEventListener("click", () => setGlobalLanguage(choice.dataset.globalLanguageChoice));
});

document.querySelectorAll("[data-copy-install]").forEach((button) => {
  button.addEventListener("click", async () => {
    const command = button.parentElement.querySelector("code").textContent;
    try { await navigator.clipboard.writeText(command); } catch { /* Preview-only fallback is unnecessary. */ }
    button.textContent = "Copied";
    window.setTimeout(() => { button.textContent = "Copy"; }, 1300);
  });
});
