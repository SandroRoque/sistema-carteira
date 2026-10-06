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

// Upload area: picking files submits at once; files dropped on the area too.
document.addEventListener("change", (event) => {
  const input = event.target;
  if (input.matches?.("input[data-enviar-ao-escolher]") && input.files.length) {
    input.form.requestSubmit();
  }
});

for (const tipo of ["dragenter", "dragover"]) {
  document.addEventListener(tipo, (event) => {
    const area = event.target.closest?.("[data-area-envio]");
    if (area) {
      event.preventDefault();
      area.classList.add("arrastando");
    }
  });
}

document.addEventListener("dragleave", (event) => {
  const area = event.target.closest?.("[data-area-envio]");
  if (area && !area.contains(event.relatedTarget)) area.classList.remove("arrastando");
});

document.addEventListener("drop", (event) => {
  const area = event.target.closest?.("[data-area-envio]");
  if (!area) return;
  event.preventDefault();
  area.classList.remove("arrastando");
  const input = area.querySelector("input[type=file]");
  if (event.dataTransfer?.files.length) {
    input.files = event.dataTransfer.files;
    area.requestSubmit();
  }
});

// Buttons with data-copiar copy their text to the clipboard.
document.addEventListener("click", async (event) => {
  const botao = event.target.closest?.("button[data-copiar]");
  if (!botao) return;
  try {
    await navigator.clipboard.writeText(botao.dataset.copiar);
    const original = botao.textContent;
    botao.textContent = "Copiado";
    setTimeout(() => { botao.textContent = original; }, 2000);
  } catch {
    botao.textContent = "Não foi possível copiar";
  }
});
