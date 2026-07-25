const selections = {
  theme: "T1",
  language: "L1",
  font: "F2",
  toc: "P1",
};

const selectionCode = document.querySelector("#selection-code");
const copySelection = document.querySelector("#copy-selection");

function selectionText(separator = " + ") {
  return [selections.theme, selections.language, selections.font, selections.toc].join(separator);
}

function updateSelectionSummary() {
  selectionCode.textContent = selectionText(" · ");
}

document.querySelectorAll("[data-option-card]").forEach((card) => {
  const button = card.querySelector("[data-choose]");
  const group = card.dataset.group;
  const value = card.dataset.value;

  button.setAttribute("aria-pressed", card.classList.contains("is-selected") ? "true" : "false");
  button.addEventListener("click", () => {
    selections[group] = value;
    document.querySelectorAll(`[data-option-card][data-group="${group}"]`).forEach((peer) => {
      const selected = peer === card;
      peer.classList.toggle("is-selected", selected);
      const peerButton = peer.querySelector("[data-choose]");
      peerButton.setAttribute("aria-pressed", selected ? "true" : "false");
      peerButton.textContent = selected ? "已选择" : `选择 ${peer.dataset.value}`;
    });
    updateSelectionSummary();
  });
});

copySelection.addEventListener("click", async () => {
  const value = selectionText();
  try {
    await navigator.clipboard.writeText(value);
    copySelection.textContent = "已复制";
  } catch {
    const input = document.createElement("textarea");
    input.value = value;
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    document.execCommand("copy");
    input.remove();
    copySelection.textContent = "已复制";
  }
  window.setTimeout(() => { copySelection.textContent = "复制选择"; }, 1400);
});

document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => {
    const canvas = button.closest("[data-theme-demo]");
    const isDark = canvas.classList.toggle("is-dark");
    button.setAttribute("aria-pressed", isDark ? "true" : "false");
    const label = button.querySelector(".spotlight-label");
    if (label) label.textContent = isDark ? "Light" : "Dark";
  });
});

function setDemoLanguage(demo, language) {
  if (demo.dataset.currentLanguage === language) return;
  demo.dataset.currentLanguage = language;
  demo.classList.add("is-switching");

  window.setTimeout(() => {
    const title = language === "zh" ? demo.dataset.titleZh : demo.dataset.titleEn;
    const copy = language === "zh" ? demo.dataset.copyZh : demo.dataset.copyEn;
    demo.querySelector("[data-demo-title]").textContent = title;
    demo.querySelector("[data-demo-copy]").textContent = copy;

    const inline = demo.querySelector(".inline-language");
    if (inline) {
      inline.classList.toggle("is-en", language === "en");
      inline.querySelectorAll("[data-language-choice]").forEach((choice) => {
        choice.classList.toggle("active", choice.dataset.languageChoice === language);
      });
    }

    const menu = demo.querySelector("[data-language-menu]");
    if (menu) {
      demo.querySelector("[data-language-label]").textContent = language === "zh" ? "简体中文" : "English";
      menu.querySelectorAll("[data-language-menu-choice]").forEach((choice) => {
        choice.classList.toggle("active", choice.dataset.languageMenuChoice === language);
      });
    }

    const flip = demo.querySelector("[data-language-toggle]");
    if (flip) {
      flip.querySelector("[data-flip-label]").textContent = language === "zh" ? "中" : "EN";
      flip.querySelector("[data-flip-target]").textContent = language === "zh" ? "EN" : "中";
      flip.classList.remove("is-flipping");
    }

    requestAnimationFrame(() => demo.classList.remove("is-switching"));
  }, 130);
}

document.querySelectorAll("[data-language-demo]").forEach((demo) => {
  demo.dataset.currentLanguage = "zh";
  demo.querySelectorAll("[data-language-choice]").forEach((choice) => {
    choice.addEventListener("click", () => setDemoLanguage(demo, choice.dataset.languageChoice));
  });

  const trigger = demo.querySelector("[data-language-menu-toggle]");
  const menu = demo.querySelector("[data-language-menu]");
  if (trigger && menu) {
    trigger.addEventListener("click", () => {
      const willOpen = menu.hidden;
      menu.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
    menu.querySelectorAll("[data-language-menu-choice]").forEach((choice) => {
      choice.addEventListener("click", () => {
        setDemoLanguage(demo, choice.dataset.languageMenuChoice);
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      });
    });
  }

  const flip = demo.querySelector("[data-language-toggle]");
  if (flip) {
    flip.addEventListener("click", () => {
      flip.classList.add("is-flipping");
      const next = demo.dataset.currentLanguage === "zh" ? "en" : "zh";
      setDemoLanguage(demo, next);
    });
  }
});

document.addEventListener("click", (event) => {
  document.querySelectorAll("[data-language-menu]").forEach((menu) => {
    const popover = menu.closest(".language-popover");
    if (menu.hidden || popover.contains(event.target)) return;
    menu.hidden = true;
    popover.querySelector("[data-language-menu-toggle]").setAttribute("aria-expanded", "false");
  });
});

document.querySelectorAll(".toc-demo").forEach((toc) => {
  const links = [...toc.querySelectorAll("[data-toc-link]")];
  links.forEach((link, index) => {
    link.setAttribute("aria-current", index === 0 ? "location" : "false");
    link.addEventListener("click", () => {
      links.forEach((peer) => {
        const active = peer === link;
        peer.classList.toggle("active", active);
        peer.setAttribute("aria-current", active ? "location" : "false");
      });
      const progress = toc.querySelector("[data-toc-progress]");
      if (progress) progress.textContent = `${String(index + 1).padStart(2, "0")} / ${String(links.length).padStart(2, "0")}`;
    });
  });
});

updateSelectionSummary();
