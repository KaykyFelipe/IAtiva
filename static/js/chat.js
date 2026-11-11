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

  // --- Envio estilo chat: mensagem na tela na hora, "pensando" e botão de parar ---------------
  let atual = null; // envio em andamento: {form, texto, elementos}

  function novoId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  function botao(form, parando) {
    const b = form.querySelector("[data-botao-enviar]");
    if (!b) return;
    b.classList.toggle("parar", parando);
    b.setAttribute("aria-label", parando ? "Parar" : "Enviar");
    b.title = parando ? "Parar" : "";
    b.textContent = parando ? "■" : "↑";
  }

  function mostrarPensando(texto) {
    const lista = document.querySelector(".mensagens");
    if (!lista) return [];
    const vazio = lista.querySelector(".vazio");
    if (vazio) vazio.hidden = true;
    const bolha = document.createElement("div");
    bolha.className = "msg usuario";
    const p = document.createElement("p");
    p.textContent = texto; // texto puro, nunca HTML
    bolha.appendChild(p);
    const pensando = document.createElement("div");
    pensando.className = "msg ia pensando";
    pensando.setAttribute("role", "status");
    pensando.innerHTML = '<span class="pontos" aria-hidden="true"><i></i><i></i><i></i></span><span>Pensando…</span>';
    lista.append(bolha, pensando);
    rolarParaFim();
    return [bolha, pensando];
  }

  function encerrar(restaurar) {
    if (!atual) return;
    const { form, texto, elementos } = atual;
    atual = null;
    elementos.forEach((el) => el.remove());
    const vazio = document.querySelector(".mensagens .vazio");
    if (vazio) vazio.hidden = false;
    botao(form, false);
    const campo = form.querySelector("[data-campo]");
    if (campo && restaurar) {
      campo.value = texto;
      campo.dispatchEvent(new Event("input"));
      campo.focus();
    }
  }

  function parar() {
    if (!atual) return;
    const { form } = atual;
    const dados = new FormData();
    dados.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
    dados.append("pedido", form.querySelector("[data-pedido]").value);
    // Marca primeiro no servidor; depois corta a espera da resposta no navegador.
    fetch(form.dataset.pararUrl, { method: "POST", body: dados, credentials: "same-origin" })
      .catch(() => {})
      .finally(() => {
        if (window.htmx) htmx.trigger(form, "htmx:abort");
        encerrar(true);
      });
  }

  // Captura: roda antes do htmx ler o formulário.
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.matches("[data-compositor]")) return;
    if (atual) {
      e.preventDefault();
      e.stopImmediatePropagation();
      parar();
      return;
    }
    const campo = form.querySelector("[data-campo]");
    const texto = campo ? campo.value.trim() : "";
    if (!texto) return;
    form.querySelector("[data-pedido]").value = novoId();
    atual = { form, texto, elementos: mostrarPensando(texto) };
    botao(form, true);
  }, true);

  document.body.addEventListener("htmx:beforeRequest", (e) => {
    // O htmx já leu os valores: pode limpar o campo.
    if (atual && e.detail.elt === atual.form) {
      const campo = atual.form.querySelector("[data-campo]");
      if (campo) { campo.value = ""; campo.dispatchEvent(new Event("input")); }
    }
  });
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach((ev) =>
    document.body.addEventListener(ev, (e) => {
      if (atual && e.detail.elt === atual.form) encerrar(true);
    }));
  document.body.addEventListener("htmx:afterRequest", (e) => {
    // 204 = envio interrompido no servidor: a tela já foi restaurada.
    if (atual && e.detail.elt === atual.form && e.detail.xhr && e.detail.xhr.status === 204) encerrar(true);
  });

  document.addEventListener("DOMContentLoaded", () => {
    rolarParaFim();
    prepararCampo();
  });
  document.body.addEventListener("htmx:afterSwap", () => {
    atual = null; // a conversa nova já traz o formulário no estado "enviar"
    rolarParaFim();
    prepararCampo();
  });
})();
