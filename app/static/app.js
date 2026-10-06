// Behaviour that would otherwise need inline handlers (blocked by the CSP).

// Forms marked data-limpar-apos-sucesso reset after a successful HTMX request.
document.addEventListener("htmx:afterRequest", (event) => {
  const form = event.detail.elt;
  if (event.detail.successful && form.matches?.("form[data-limpar-apos-sucesso]")) {
    form.reset();
  }
});

// Portfolio switcher: submit as soon as a different portfolio is picked.
document.addEventListener("change", (event) => {
  const select = event.target;
  if (select.matches?.("select[data-trocar-carteira]")) {
    select.form.action = `/carteiras/${select.value}/selecionar`;
    select.form.submit();
  }
});
