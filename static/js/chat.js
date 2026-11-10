// Comportamentos da tela de chat. Não guarda nada no navegador.
(function () {
  const app = document.getElementById("app");

  function rolarParaFim() {
    const r = document.querySelector("[data-rolagem]");
    if (r) r.scrollTop = r.scrollHeight;
  }

  function prepararCampo() {
    const campo = document.querySelector("[data-campo]");
    if (!campo) return;
    const ajustar = () => {
      campo.style.height = "auto";
      campo.style.height = Math.min(campo.scrollHeight, 200) + "px";
    };
    campo.addEventListener("input", ajustar);
    campo.addEventListener("keydown", (e) => {
      // Enter envia; Shift+Enter quebra a linha.
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (campo.value.trim()) campo.form.requestSubmit();
      }
    });
    campo.focus();
  }

  document.addEventListener("click", (e) => {
    const alvo = e.target.closest("[data-sugestao], [data-abrir-lateral], [data-fechar-lateral]");
    if (!alvo) return;
    if (alvo.hasAttribute("data-abrir-lateral")) app.classList.add("menu-aberto");
    if (alvo.hasAttribute("data-fechar-lateral")) app.classList.remove("menu-aberto");
    if (alvo.hasAttribute("data-sugestao")) {
      const campo = document.querySelector("[data-campo]");
      if (campo) {
        campo.value = alvo.getAttribute("data-sugestao");
        campo.focus();
      }
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    rolarParaFim();
    prepararCampo();
  });
  document.body.addEventListener("htmx:afterSwap", () => {
    rolarParaFim();
    prepararCampo();
  });
})();
