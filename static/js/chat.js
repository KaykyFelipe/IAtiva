// Comportamentos da tela de chat. Não guarda nada no navegador.
(function () {
  const app = document.getElementById("app");
  const grupos = new Map();
  let busca = "";
  let larguraLateral = parseInt(app.dataset.largura, 10) || 278;

  let salvarTimer = null;
  function salvarLayout() {
    // Lembrado na sessão do servidor (só layout, nenhum dado), para valer ao trocar de conversa.
    clearTimeout(salvarTimer);
    salvarTimer = setTimeout(() => {
      fetch(app.dataset.layoutUrl, {
        method: "POST", headers: {"X-CSRFToken": csrf(), "Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({
          largura: larguraLateral,
          ampla: app.classList.contains("leitura-ampla") ? "1" : "",
          recolhida: app.classList.contains("lateral-recolhida") ? "1" : "",
        }),
      }).catch(() => {});
    }, 300);
  }

  function saudacao(hora) {
    return hora >= 5 && hora < 12 ? "Bom dia" : hora >= 12 && hora < 18 ? "Boa tarde" : "Boa noite";
  }

  function atualizarSaudacao() {
    document.querySelectorAll("[data-saudacao]").forEach((el) => {
      el.textContent = saudacao(new Date().getHours()) + ", " + el.dataset.nome;
    });
  }

  function filtrarConversas() {
    const termo = busca.trim().toLocaleLowerCase("pt-BR");
    document.querySelectorAll("[data-item-busca]").forEach((el) => {
      el.hidden = !el.textContent.toLocaleLowerCase("pt-BR").includes(termo);
    });
  }

  function prepararLateral() {
    document.querySelectorAll("[data-grupo]").forEach((el) => {
      if (grupos.has(el.dataset.grupo)) el.open = grupos.get(el.dataset.grupo);
    });
    const campo = document.querySelector("[data-busca-conversas]");
    if (campo) campo.value = busca;
    filtrarConversas();
    const divisor = document.querySelector("[data-redimensionar]");
    if (divisor) divisor.setAttribute("aria-valuenow", larguraLateral);
    document.querySelectorAll("[data-expandir-leitura]").forEach((b) => {
      b.setAttribute("aria-pressed", String(app.classList.contains("leitura-ampla")));
    });
  }

  function redimensionar(valor) {
    larguraLateral = Math.max(224, Math.min(430, valor));
    app.style.setProperty("--largura-lateral", larguraLateral + "px");
    const divisor = document.querySelector("[data-redimensionar]");
    if (divisor) divisor.setAttribute("aria-valuenow", larguraLateral);
    salvarLayout();
  }


  function csrf() {
    const campo = document.querySelector("input[name=csrfmiddlewaretoken]");
    return campo ? campo.value : "";
  }

  function renomear(botao) {
    const link = botao.closest("a");
    const titulo = link.querySelector("[data-titulo]");
    if (!titulo || link.querySelector("input.renomeando")) return;
    const original = titulo.textContent;
    const campo = document.createElement("input");
    campo.className = "renomeando";
    campo.maxLength = 80;
    campo.value = original;
    campo.setAttribute("aria-label", "Novo nome da conversa");
    titulo.hidden = true;
    titulo.after(campo);
    campo.focus();
    campo.select();
    let feito = false;
    const fechar = (novo) => {
      if (feito) return;
      feito = true;
      titulo.textContent = novo;
      titulo.hidden = false;
      campo.remove();
    };
    const salvar = () => {
      const novo = campo.value.trim();
      if (!novo || novo === original) return fechar(original);
      fetch(botao.dataset.renomear, {
        method: "POST", headers: {"X-CSRFToken": csrf(), "Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({titulo: novo}),
      }).then((r) => r.ok ? r.text() : Promise.reject()).then((t) => {
        fechar(t);
        if (link.classList.contains("ativa")) {
          const h1 = document.querySelector(".titulo-conversa h1");
          if (h1) h1.textContent = t;
        }
      }).catch(() => fechar(original));
    };
    ["click", "mousedown"].forEach((ev) => campo.addEventListener(ev, (e) => { e.preventDefault(); e.stopPropagation(); }));
    campo.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); salvar(); }
      if (e.key === "Escape") { e.preventDefault(); fechar(original); }
    });
    campo.addEventListener("blur", salvar);
  }

  document.addEventListener("toggle", (e) => {
    if (e.target.matches("[data-grupo]")) grupos.set(e.target.dataset.grupo, e.target.open);
  }, true);
  document.addEventListener("input", (e) => {
    if (e.target.matches("[data-busca-conversas]")) { busca = e.target.value; filtrarConversas(); }
  });
  document.addEventListener("pointerdown", (e) => {
    const divisor = e.target.closest("[data-redimensionar]");
    if (!divisor || window.innerWidth <= 680) return;
    divisor.setPointerCapture(e.pointerId);
    divisor.classList.add("arrastando");
    document.body.classList.add("redimensionando");
  });
  document.addEventListener("pointermove", (e) => {
    const divisor = document.querySelector("[data-redimensionar].arrastando");
    if (divisor && divisor.hasPointerCapture(e.pointerId)) redimensionar(e.clientX);
  });
  function encerrarArraste() {
    const divisor = document.querySelector("[data-redimensionar].arrastando");
    if (divisor) divisor.classList.remove("arrastando");
    document.body.classList.remove("redimensionando");
  }
  document.addEventListener("pointerup", encerrarArraste);
  document.addEventListener("pointercancel", encerrarArraste);
  document.addEventListener("lostpointercapture", encerrarArraste);
  document.addEventListener("dblclick", (e) => { if (e.target.closest("[data-redimensionar]")) redimensionar(278); });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("[data-redimensionar]")) {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); redimensionar(larguraLateral + (e.key === "ArrowRight" ? 16 : -16)); }
      if (e.key === "Home") { e.preventDefault(); redimensionar(278); }
    }
    if (e.key === "Escape") app.classList.remove("menu-aberto");
    const editando = e.target.matches("input, textarea, select, [contenteditable=true]");
    if (e.key === "/" && !editando) {
      e.preventDefault();
      app.classList.remove("lateral-recolhida");
      if (innerWidth <= 680) app.classList.add("menu-aberto");
      const campo = document.querySelector("[data-busca-conversas]");
      if (campo) campo.focus();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      const link = document.querySelector(".nova");
      if (link) { e.preventDefault(); window.location.assign(link.href); }
    }
  });

  function rolarParaFim() {
    const r = document.querySelector("[data-rolagem]");
    if (r) r.scrollTop = r.scrollHeight;
  }

  function prepararCampo() {
    const campo = document.querySelector("[data-campo]");
    if (!campo || campo.dataset.preparado) return;
    campo.dataset.preparado = "1";
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
    ajustar();
    if (innerWidth > 680 && !document.activeElement.matches("input, textarea, select")) campo.focus();
  }

  document.addEventListener("click", (e) => {
    const lapis = e.target.closest("[data-renomear]");
    if (lapis) { e.preventDefault(); e.stopPropagation(); renomear(lapis); return; }
    if (e.target.closest("input.renomeando")) { e.preventDefault(); return; }
    const alvo = e.target.closest("[data-sugestao], [data-abrir-lateral], [data-fechar-lateral], [data-recolher-lateral], [data-expandir-leitura]");
    if (!alvo) return;
    if (alvo.hasAttribute("data-abrir-lateral")) {
      if (innerWidth <= 680) app.classList.add("menu-aberto");
      else app.classList.remove("lateral-recolhida");
      salvarLayout();
    }
    if (alvo.hasAttribute("data-fechar-lateral")) app.classList.remove("menu-aberto");
    if (alvo.hasAttribute("data-recolher-lateral")) {
      if (innerWidth <= 680) app.classList.remove("menu-aberto");
      else app.classList.add("lateral-recolhida");
      salvarLayout();
    }
    if (alvo.hasAttribute("data-expandir-leitura")) {
      const ampla = app.classList.toggle("leitura-ampla");
      alvo.setAttribute("aria-pressed", String(ampla));
      alvo.title = ampla ? "Restaurar largura de leitura" : "Expandir área de leitura";
      salvarLayout();
    }
    if (alvo.hasAttribute("data-sugestao")) {
      const campo = document.querySelector("[data-campo]");
      if (campo) {
        campo.value = alvo.getAttribute("data-sugestao");
        campo.dispatchEvent(new Event("input"));
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
    b.formNoValidate = parando;
    b.replaceChildren();
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "simbolo-svg"); svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", parando ? "#icone-parar" : "#icone-enviar");
    svg.append(use); b.append(svg);
  }

  function mostrarPensando(texto) {
    const lista = document.querySelector(".mensagens");
    if (!lista) return [];
    const vazio = lista.querySelector(".vazio");
    if (vazio) vazio.hidden = true;
    document.getElementById("conversa").classList.remove("inicio");
    const sugestoes = document.querySelector("[data-sugestoes-inicio]");
    if (sugestoes) sugestoes.hidden = true;
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
    if (vazio) {
      vazio.hidden = false;
      document.getElementById("conversa").classList.add("inicio");
      const sugestoes = document.querySelector("[data-sugestoes-inicio]");
      if (sugestoes) sugestoes.hidden = false;
    }
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
    // O registro pode esperar um lock do banco; ele não deve atrasar a interrupção visual.
    fetch(form.dataset.pararUrl, { method: "POST", body: dados, credentials: "same-origin" })
      .catch(() => {});
    if (window.htmx) htmx.trigger(form, "htmx:abort");
    encerrar(true);
  }

  document.addEventListener("click", (e) => {
    const enviar = e.target.closest("[data-botao-enviar]");
    if (atual && enviar && enviar.form === atual.form) {
      e.preventDefault(); e.stopImmediatePropagation(); parar();
    }
  }, true);

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
    atualizarSaudacao();
    prepararLateral();
    rolarParaFim();
    prepararCampo();
  });
  document.body.addEventListener("htmx:afterSwap", () => {
    if (atual && !atual.form.isConnected) atual = null;
    atualizarSaudacao();
    prepararLateral();
    rolarParaFim();
    prepararCampo();
  });
  setInterval(atualizarSaudacao, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) atualizarSaudacao(); });
  window.matchMedia("(max-width: 680px)").addEventListener("change", () => app.classList.remove("menu-aberto"));
})();
