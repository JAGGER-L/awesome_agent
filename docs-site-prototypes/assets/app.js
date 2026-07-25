const root = document.documentElement;

const savedTheme = localStorage.getItem("awesome-docs-theme");
if (savedTheme) root.dataset.theme = savedTheme;

document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("awesome-docs-theme", next);
    button.setAttribute("aria-label", `Switch to ${next === "dark" ? "light" : "dark"} mode`);
  });
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const code = button.closest(".code-block")?.querySelector("code")?.innerText ?? "";
    try {
      await navigator.clipboard.writeText(code);
      const previous = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = previous), 1400);
    } catch {
      button.textContent = "Select text";
    }
  });
});

const overlay = document.querySelector("[data-search-overlay]");
const searchInput = overlay?.querySelector("input");

function openSearch() {
  if (!overlay) return;
  overlay.hidden = false;
  document.body.classList.add("no-scroll");
  setTimeout(() => searchInput?.focus(), 0);
}

function closeSearch() {
  if (!overlay) return;
  overlay.hidden = true;
  document.body.classList.remove("no-scroll");
}

document.querySelectorAll("[data-search-open]").forEach((button) =>
  button.addEventListener("click", openSearch),
);
document.querySelectorAll("[data-search-close]").forEach((button) =>
  button.addEventListener("click", closeSearch),
);
overlay?.addEventListener("click", (event) => {
  if (event.target === overlay) closeSearch();
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSearch();
  }
  if (event.key === "Escape") closeSearch();
});

document.querySelectorAll("[data-menu-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const sidebar = document.querySelector("[data-sidebar]");
    const expanded = sidebar?.classList.toggle("is-open") ?? false;
    button.setAttribute("aria-expanded", String(expanded));
  });
});

document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => {
    const current = button.textContent?.trim();
    button.textContent = current === "EN" ? "中" : "EN";
  });
});
