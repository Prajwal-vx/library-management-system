(() => {
  const button = document.querySelector(".mobile-menu-toggle");
  const navigation = document.querySelector("#primary-navigation");
  if (!button || !navigation) return;
  button.addEventListener("click", () => {
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    navigation.classList.toggle("is-open", !expanded);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || button.getAttribute("aria-expanded") !== "true") return;
    button.setAttribute("aria-expanded", "false");
    navigation.classList.remove("is-open");
    button.focus();
  });
})();
