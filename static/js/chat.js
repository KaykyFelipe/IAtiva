// Comportamentos da tela de chat. Não guarda nada no navegador.
(function () {
  const app = document.getElementById("app");
  const grupos = new Map();
  let busca = "";
  let larguraLateral = parseInt(app.dataset.largura, 10) || 278;
  // O chat escolhe o fim ao abrir e mantém sua própria posição nas atualizações.
  history.scrollRestoration = "manual";
  // A CSP bloqueia o style inline vindo do HTML; aplicar a largura pelo script autorizado.
  app.style.setProperty("--largura-lateral", larguraLateral + "px");

  let salvarTimer = null;
  function salvarLayout(imediato = false) {
    // Lembrado na sessão do servidor (só layout, nenhum dado), para valer ao trocar de conversa.
    clearTimeout(salvarTimer);
    const gravar = () => {
      fetch(app.dataset.layoutUrl, {
        method: "POST", keepalive: true, credentials: "same-origin",
        headers: {"X-CSRFToken": csrf(), "Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams({
          largura: larguraLateral,
          ampla: app.classList.contains("leitura-ampla") ? "1" : "",
          recolhida: app.classList.contains("lateral-recolhida") ? "1" : "",
        }),
      }).catch(() => {});
    };
    if (imediato) gravar(); else salvarTimer = setTimeout(gravar, 300);
  }
  window.addEventListener("pagehide", () => { if (salvarTimer) salvarLayout(true); });

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
    campo.focus({ preventScroll: true });
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
    if (divisor) { divisor.classList.remove("arrastando"); salvarLayout(true); }
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
      if (campo) campo.focus({ preventScroll: true });
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      const link = document.querySelector(".nova");
      if (link) { e.preventDefault(); window.location.assign(link.href); }
    }
  });

  function capturarLeitura(area) {
    const painel = area?.querySelector("[data-rolagem]");
    if (!painel) return null;
    const resultados = new Map();
    const topoPainel = painel.getBoundingClientRect().top;
    const mensagem = Array.from(painel.querySelectorAll("[data-mensagem-id]")).find((el) =>
      el.getBoundingClientRect().bottom > topoPainel);
    const ancora = mensagem ? { id: mensagem.dataset.mensagemId, distancia: mensagem.getBoundingClientRect().top - topoPainel } : null;
    for (const resposta of painel.querySelectorAll("[data-resultado-de]")) {
      resultados.set(resposta.dataset.resultadoDe, {
        tabelas: Array.from(resposta.querySelectorAll(".tabela-resultado"),
          (el) => ({ topo: el.scrollTop, esquerda: el.scrollLeft, foco: document.activeElement === el })),
        detalhes: Array.from(resposta.querySelectorAll("details"), (el) => el.open),
      });
    }
    return { conversa: area.dataset.conversaId, topo: painel.scrollTop, noFim: distanciaAteFim(painel) <= 24, resultados, ancora };
  }

  function restaurarLeitura(area, vista) {
    const painel = area?.querySelector("[data-rolagem]");
    if (!painel || !vista || vista.conversa !== area.dataset.conversaId) return false;
    const ancora = vista.ancora && painel.querySelector(`[data-mensagem-id="${vista.ancora.id}"]`);
    for (const resposta of painel.querySelectorAll("[data-resultado-de]")) {
      const estado = vista.resultados.get(resposta.dataset.resultadoDe);
      if (!estado) continue;
      resposta.querySelectorAll("details").forEach((el, i) => {
        if (i < estado.detalhes.length) el.open = estado.detalhes[i];
      });
      resposta.querySelectorAll(".tabela-resultado").forEach((el, i) => {
        const posicao = estado.tabelas[i];
        if (!posicao) return;
        el.scrollTop = posicao.topo;
        el.scrollLeft = posicao.esquerda;
        if (posicao.foco) el.focus({ preventScroll: true });
      });
    }
    painel.scrollTop = vista.noFim && area.dataset.conversaId ? painel.scrollHeight : ancora
      ? painel.scrollTop + ancora.getBoundingClientRect().top - painel.getBoundingClientRect().top - vista.ancora.distancia
      : vista.topo;
    atualizarAtalhoFim();
    return true;
  }

  function distanciaAteFim(painel) {
    return painel.scrollHeight - painel.clientHeight - painel.scrollTop;
  }

  function atualizarAtalhoFim() {
    const painel = document.querySelector("[data-rolagem]");
    const botao = document.querySelector("[data-ir-ao-fim]");
    if (painel && botao) botao.hidden = distanciaAteFim(painel) <= 48;
  }

  function rolarParaFim(behavior = "instant") {
    const r = document.querySelector("[data-rolagem]");
    if (r && !r.closest(".memoria-area")) r.scrollTo({ top: r.scrollHeight, behavior });
    atualizarAtalhoFim();
  }

  document.addEventListener("scroll", (e) => {
    if (e.target.matches?.("[data-rolagem]")) {
      atualizarAtalhoFim();
      document.querySelector(".menu-copia:popover-open")?.hidePopover();
    }
  }, true);
  window.addEventListener("resize", atualizarAtalhoFim);
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-ir-ao-fim]")) {
      rolarParaFim(window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth");
    }
  });

  function prepararCampo(autofoco = true) {
    const campo = document.querySelector("[data-campo]");
    if (!campo || campo.dataset.preparado) return;
    campo.dataset.preparado = "1";
    const avisoLimite = campo.form.querySelector("#limite-mensagem");
    const limite = Number(campo.dataset.limiteMensagem);
    const ajustar = () => {
      const painel = document.querySelector("[data-rolagem]");
      const noFim = painel && distanciaAteFim(painel) <= 24;
      if (avisoLimite && limite > 0) {
        const excedeu = Array.from(campo.value.trim()).length > limite;
        avisoLimite.hidden = !excedeu;
        campo.setCustomValidity(excedeu ? `A mensagem pode ter até ${limite} caracteres.` : "");
      }
      campo.style.height = "auto";
      campo.style.height = campo.scrollHeight + "px";
      if (noFim) painel.scrollTop = painel.scrollHeight;
      atualizarAtalhoFim();
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
    if (autofoco && innerWidth > 680 && !document.activeElement.matches("input, textarea, select")) campo.focus({ preventScroll: true });
  }

  // M71: excluir conversa, com confirmação.
  function confirmar(titulo, texto, rotulo) {
    return new Promise((resolver) => {
      const fundo = document.createElement("div");
      fundo.className = "confirmar-fundo";
      fundo.innerHTML = '<div class="confirmar-caixa" role="dialog" aria-modal="true" aria-labelledby="confirmar-titulo">' +
        '<h2 id="confirmar-titulo"></h2><p></p><div class="confirmar-acoes">' +
        '<button type="button" data-cancelar>Cancelar</button><button type="button" class="perigo" data-ok></button></div></div>';
      fundo.querySelector("h2").textContent = titulo; // texto puro
      fundo.querySelector("p").textContent = texto;
      fundo.querySelector("[data-ok]").textContent = rotulo;
      const fechar = (sim) => { document.removeEventListener("keydown", tecla, true); fundo.remove(); resolver(sim); };
      const tecla = (ev) => { if (ev.key === "Escape") { ev.preventDefault(); fechar(false); } };
      fundo.addEventListener("click", (ev) => {
        if (ev.target === fundo || ev.target.closest("[data-cancelar]")) fechar(false);
        else if (ev.target.closest("[data-ok]")) fechar(true);
      });
      document.addEventListener("keydown", tecla, true);
      document.body.append(fundo);
      fundo.querySelector("[data-cancelar]").focus();
    });
  }
  async function excluirConversa(botao) {
    const item = botao.closest("a.conversa");
    const nome = item ? (item.querySelector("[data-titulo]") || item).textContent.trim() : "esta conversa";
    if (!(await confirmar("Excluir conversa?", `“${nome}” e todas as mensagens dela serão apagadas. Isso não pode ser desfeito.`, "Excluir"))) return;
    const dados = new FormData();
    const token = document.querySelector("[name=csrfmiddlewaretoken]");
    if (token) dados.append("csrfmiddlewaretoken", token.value);
    const r = await fetch(botao.dataset.excluir, { method: "POST", body: dados, credentials: "same-origin" }).catch(() => null);
    if (!r || !r.ok) return;
    const ativa = item && item.classList.contains("ativa");
    if (item) item.remove();
    if (ativa) abrirConversa(document.querySelector("a.nova").href);
  }

  // M71: trocar de conversa sem recarregar a página (só a área da conversa é trocada).
  function abrirConversa(url, empilhar = true) {
    if (!window.htmx || !document.getElementById("conversa")) { location.href = url; return; }
    // A lateral volta junto (título/estado); mantém a rolagem dela onde estava.
    const nav = document.querySelector("#lateral nav");
    const rolagem = nav ? nav.scrollTop : 0;
    htmx.ajax("GET", url, { target: "#conversa", swap: "outerHTML" }).then(() => {
      const nova = document.querySelector("#lateral nav");
      if (nova) { nova.scrollTop = rolagem; requestAnimationFrame(() => { nova.scrollTop = rolagem; }); }
      if (empilhar) history.pushState({ conversa: true }, "", url);
      app.classList.remove("menu-aberto");
    });
  }
  window.addEventListener("popstate", () => abrirConversa(location.pathname + location.search, false));

  document.addEventListener("click", (e) => {
    const lixeira = e.target.closest("[data-excluir]");
    if (lixeira) { e.preventDefault(); e.stopPropagation(); excluirConversa(lixeira); return; }
    const link = e.target.closest("#lateral a.conversa, #lateral a.nova, [data-abrir-chat]");
    if (link && !e.target.closest("[data-renomear]") && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.button === 0
        && !document.querySelector(".renomeando")) {
      e.preventDefault();
      document.querySelectorAll("#lateral a.conversa.ativa").forEach((a) => a.classList.remove("ativa"));
      if (link.classList.contains("conversa")) link.classList.add("ativa");
      abrirConversa(link.href);
      return;
    }
    const secao = e.target.closest(".memoria-navegacao a");
    if (secao && e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      document.getElementById(secao.hash.slice(1))?.scrollIntoView({ block: "start" });
      return;
    }
    const lapis = e.target.closest("[data-renomear]");
    if (lapis) { e.preventDefault(); e.stopPropagation(); renomear(lapis); return; }
    if (e.target.closest("input.renomeando")) { e.preventDefault(); return; }
    const alvo = e.target.closest("[data-sugestao], [data-abrir-lateral], [data-fechar-lateral], [data-recolher-lateral], [data-expandir-leitura]");
    if (!alvo) return;
    if (alvo.hasAttribute("data-abrir-lateral")) {
      if (innerWidth <= 680) app.classList.add("menu-aberto");
      else app.classList.remove("lateral-recolhida");
      salvarLayout(true);
    }
    if (alvo.hasAttribute("data-fechar-lateral")) app.classList.remove("menu-aberto");
    if (alvo.hasAttribute("data-recolher-lateral")) {
      if (innerWidth <= 680) app.classList.remove("menu-aberto");
      else app.classList.add("lateral-recolhida");
      salvarLayout(true);
    }
    if (alvo.hasAttribute("data-expandir-leitura")) {
      const ampla = app.classList.toggle("leitura-ampla");
      alvo.setAttribute("aria-pressed", String(ampla));
      alvo.title = ampla ? "Restaurar largura de leitura" : "Expandir área de leitura";
      salvarLayout(true);
    }
    if (alvo.hasAttribute("data-sugestao")) {
      const campo = document.querySelector("[data-campo]");
      if (campo) {
        campo.value = alvo.getAttribute("data-sugestao");
        campo.dispatchEvent(new Event("input"));
        campo.focus({ preventScroll: true });
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

  function montarPensando(pensando) {
    // M69: etapas recolhidas atrás de uma seta ao lado do passo atual (clica e abre; clica de novo e fecha).
    pensando.innerHTML = '<ol class="pensando-passos" data-pensando-passos hidden></ol>' +
      '<div class="pensando-linha"><span class="pontos" aria-hidden="true"><i></i><i></i><i></i></span>' +
      '<span class="pensando-atual" data-pensando-atual>Pensando…</span>' +
      '<button type="button" class="pensando-seta" data-pensando-seta aria-expanded="false" ' +
      'aria-label="Mostrar as etapas" title="Mostrar as etapas" hidden>' +
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>';
    pensando.querySelector("[data-pensando-seta]").addEventListener("click", (ev) => {
      const b = ev.currentTarget, aberto = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", String(aberto));
      b.setAttribute("aria-label", aberto ? "Esconder as etapas" : "Mostrar as etapas");
      b.title = b.getAttribute("aria-label");
      pensando.querySelector("[data-pensando-passos]").hidden = !aberto;
    });
  }

  function mostrarPensando(texto) {
    const lista = document.querySelector(".mensagens");
    if (!lista) return [];
    const painel = lista.closest("[data-rolagem]");
    const noFim = painel && distanciaAteFim(painel) <= 24;
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
    pensando.setAttribute("aria-live", "polite");
    montarPensando(pensando);
    lista.append(bolha, pensando);
    if (noFim) rolarParaFim();
    else atualizarAtalhoFim();
    animarProxima = true;
    return [bolha, pensando];
  }

  // M26: o "Pensando…" mostra o que a IA está fazendo agora (texto operacional vindo do servidor).
  let acompanhamento = null;
  function acompanhar(form, pedido) {
    pararAcompanhamento();
    const base = form.dataset.andamentoUrl;
    if (!base || !pedido) return;
    const url = base.replace("00000000-0000-0000-0000-000000000000", pedido);
    let vistos = 0;
    const ler = () => fetch(url, { credentials: "same-origin", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((dados) => {
        if (!dados || (!atual && !document.querySelector("[data-envio-pendente]"))) return;
        const bolha = document.querySelector(".msg.pensando");
        if (!bolha) return;
        const passos = dados.passos || [];
        if (passos.length === vistos) return;
        const lista = bolha.querySelector("[data-pensando-passos]");
        const agora = bolha.querySelector("[data-pensando-atual]");
        for (let i = Math.max(0, vistos - 1); i < passos.length - 1; i++) {
          if (i < vistos - 1) continue;
          // M68: um passo já listado não se repete (antes "Conferindo as junções" aparecia várias vezes).
          if (Array.from(lista.children).some((li) => li.textContent === passos[i])) continue;
          const li = document.createElement("li");
          li.textContent = passos[i]; // texto puro
          lista.append(li);
        }
        const seta = bolha.querySelector("[data-pensando-seta]");
        if (seta) seta.hidden = !lista.children.length;
        // M70: consulta filial por filial não troca o texto de cima a cada filial (o detalhe fica na seta).
        const ultimo = passos[passos.length - 1];
        const texto = /^(Consultando o ERP —|Conferindo se as junções|Calculando o total no banco)/.test(ultimo)
          ? "Consultando no banco" : ultimo;
        const mudouTexto = agora.textContent !== texto + "…";
        agora.textContent = texto + "…";
        if (mudouTexto) { agora.classList.remove("trocou"); void agora.offsetWidth; agora.classList.add("trocou"); }
        vistos = passos.length;
      })
      .catch(() => {});
    acompanhamento = setInterval(ler, 700);
  }

  // M79: página recarregada (ou reaberta) com a IA ainda trabalhando: mostra o "trabalhando" e acompanha.
  function retomarEnvio() {
    const el = document.querySelector("[data-envio-pendente]");
    const form = document.querySelector("[data-compositor]");
    if (!el || atual || el.dataset.montado || !form) return;
    el.dataset.montado = "1";
    montarPensando(el);
    acompanhar(form, el.dataset.pedido);
    atualizarBotaoTarefa();
  }
  function pararAcompanhamento() {
    if (acompanhamento) clearInterval(acompanhamento);
    acompanhamento = null;
  }

  function encerrar(restaurar) {
    if (!atual) return;
    const { form, texto, elementos, rascunho } = atual;
    atual = null;
    pararAcompanhamento();
    setTimeout(atualizarBotaoTarefa, 0);
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
    if (campo) {
      campo.disabled = false;
      if (campo.dataset.placeholderOriginal) campo.placeholder = campo.dataset.placeholderOriginal;
    }
    if (campo && restaurar) {
      campo.value = rascunho ?? texto;
      campo.dispatchEvent(new Event("input"));
      campo.focus({ preventScroll: true });
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

  // M26: com uma verificação em segundo plano rodando, o botão de enviar vira "parar" e cancela a tarefa.
  // M50: um pedido em segundo plano (continuação ou correção pela manutenção) ocupa o chat, como uma
  // tarefa: o botão vira "parar" e outra pergunta vai para um chat novo.
  function tarefaAtiva() { return document.querySelector("#atividade-fixa[data-tarefa-ativa], [data-objetivo-pendente], [data-envio-pendente]"); }
  function atualizarBotaoTarefa() {
    const form = document.querySelector("[data-compositor]");
    if (!form || atual) return;
    const ativa = tarefaAtiva();
    botao(form, Boolean(ativa));
    const b = form.querySelector("[data-botao-enviar]");
    const objetivo = ativa && (ativa.hasAttribute("data-objetivo-pendente") || ativa.hasAttribute("data-envio-pendente"));
    if (b && ativa) {
      const rotulo = objetivo ? "Parar este pedido" : "Parar a verificação";
      b.setAttribute("aria-label", rotulo); b.title = rotulo;
    }
    const campo = form.querySelector("[data-campo]");
    if (campo) {
      if (!campo.dataset.placeholderOriginal) campo.dataset.placeholderOriginal = campo.placeholder;
      campo.disabled = Boolean(objetivo);
      campo.placeholder = objetivo ? "Estou trabalhando neste pedido. Para perguntar outra coisa, abra um novo chat."
        : campo.dataset.placeholderOriginal;
    }
  }
  function cancelarTarefa(form, barra) {
    if (barra.hasAttribute("data-envio-pendente")) {
      const dados = new FormData();
      dados.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
      dados.append("pedido", barra.dataset.pedido);
      const agora = barra.querySelector("[data-pensando-atual]");
      if (agora) agora.textContent = "Parando…";
      fetch(barra.dataset.pararUrl, { method: "POST", body: dados, credentials: "same-origin" })
        .then((r) => {
          if (!r.ok) return;
          // M94: parou: a mensagem sem resposta sai na hora e o chat destrava.
          const anterior = barra.previousElementSibling;
          if (anterior && anterior.classList.contains("usuario")) anterior.remove();
          barra.remove();
          pararAcompanhamento();
          atualizarBotaoTarefa();
        })
        .catch(() => {});
      return;
    }
    if (barra.hasAttribute("data-objetivo-pendente")) {
      const texto = barra.querySelector(".atividade-texto");
      if (texto) texto.textContent = "Parando o pedido…";
      htmx.ajax("POST", barra.dataset.cancelarUrl, { target: "#conversa", swap: "outerHTML",
        values: { csrfmiddlewaretoken: form.querySelector("[name=csrfmiddlewaretoken]").value } });
      return;
    }
    const dados = new FormData();
    dados.append("csrfmiddlewaretoken", form.querySelector("[name=csrfmiddlewaretoken]").value);
    const texto = barra.querySelector(".atividade-texto");
    if (texto) texto.textContent = "Cancelando a verificação…";
    fetch(barra.dataset.cancelarUrl, { method: "POST", body: dados, credentials: "same-origin" })
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => {
        const cartao = document.getElementById(`tarefa-${barra.dataset.tarefaId}`);
        if (html && cartao) { cartao.outerHTML = html; }
        barra.removeAttribute("data-tarefa-ativa");
        barra.hidden = true;
        atualizarBotaoTarefa();
      })
      .catch(() => {});
  }

  document.addEventListener("click", (e) => {
    const enviar = e.target.closest("[data-botao-enviar]");
    if (atual && enviar && enviar.form === atual.form) {
      e.preventDefault(); e.stopImmediatePropagation(); parar();
      return;
    }
    const barra = tarefaAtiva();
    if (!atual && enviar && barra) {
      e.preventDefault(); e.stopImmediatePropagation(); cancelarTarefa(enviar.form, barra);
    }
  }, true);

  function copiarTexto(texto) {
    const campo = document.createElement("textarea");
    const foco = document.activeElement;
    campo.value = texto;
    campo.readOnly = true;
    campo.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.append(campo);
    campo.select();
    try { return document.execCommand("copy"); }
    finally {
      campo.remove();
      foco?.focus({ preventScroll: true });
    }
  }

  function textoDaTabela(tabela) {
    return Array.from(tabela.rows, (linha) =>
      Array.from(linha.cells, (celula) =>
        celula.textContent.replace(/\s+/g, " ").trim().replaceAll("|", "\\|")
      ).join(" | ")
    ).join("\n");
  }

  function quantidadeDeLinhas(tabela) {
    return Array.from(tabela.tBodies).reduce((total, corpo) => total + corpo.rows.length, 0);
  }

  function textoDaResposta(botao) {
    const resposta = botao.closest(".resposta-chat");
    const conteudo = resposta.querySelector("[data-copiar-conteudo]").cloneNode(true);
    const conclusao = resposta.querySelector(".tarefa-conclusao");
    if (conclusao && !conclusao.querySelector(".tarefa-carregando")) conteudo.append(conclusao.cloneNode(true));
    conteudo.querySelectorAll("form, button, link, svg, [data-estado-copia]").forEach((el) => el.remove());
    for (const detalhe of conteudo.querySelectorAll("details")) {
      const secao = document.createElement("section");
      secao.append(...detalhe.childNodes);
      detalhe.replaceWith(secao);
    }
    for (const rolagem of conteudo.querySelectorAll(".tabela-rolagem, .conclusao-tabela")) {
      rolagem.replaceWith(...rolagem.childNodes);
    }
    for (const tabela of conteudo.querySelectorAll("table")) {
      const textoTabela = document.createElement("pre");
      textoTabela.textContent = textoDaTabela(tabela);
      tabela.replaceWith(textoTabela);
    }
    conteudo.querySelectorAll("br").forEach((el) => el.replaceWith("\n"));
    return Array.from(conteudo.querySelectorAll("p,h1,h2,h3,h4,dt,dd,summary,li,pre"),
      (el) => el.textContent.trim()).filter(Boolean).join("\n\n");
  }

  async function copiar(botao, tabela) {
    const texto = tabela ? textoDaTabela(tabela) : textoDaResposta(botao);
    const titulo = botao.getAttribute("aria-label");
    let copiado = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(texto);
        copiado = true;
      }
    } catch (_) { /* HTTP interno e navegadores sem acesso à área de transferência usam a seleção. */ }
    if (!copiado) {
      try { copiado = copiarTexto(texto); } catch (_) { /* permissão negada */ }
    }
    const feedback = !copiado ? "Cópia indisponível" : tabela ? `Tabela copiada (${quantidadeDeLinhas(tabela)} linhas de dados)` : "Resposta copiada";
    botao.title = feedback;
    botao.classList.toggle("copiado", copiado);
    botao.closest(".acoes-resposta").querySelector("[data-estado-copia]").textContent = feedback;
    setTimeout(() => { botao.title = titulo; botao.classList.remove("copiado"); }, 1600);
  }

  function abrirMenuCopia(botao, tabelas) {
    const menu = botao.parentElement.querySelector("[data-menu-copia]");
    if (menu.matches(":popover-open")) {
      menu.hidePopover();
      return;
    }
    const opcoes = [null, ...tabelas].map((tabela, i) => {
      const opcao = document.createElement("button");
      const titulo = tabela?.closest(".bloco-setor")?.querySelector(":scope > h3")?.textContent.trim()
        || (tabela?.closest(".tarefa-conclusao") ? "registros verificados" : tabelas.length > 1 ? `tabela ${i}` : "tabela");
      opcao.type = "button";
      opcao.textContent = tabela ? `Copiar ${titulo}` : "Copiar resposta inteira";
      opcao.addEventListener("click", () => {
        menu.hidePopover();
        botao.focus({ preventScroll: true });
        copiar(botao, tabela);
      });
      return opcao;
    });
    menu.replaceChildren(...opcoes);
    menu.ontoggle = () => botao.setAttribute("aria-expanded", String(menu.matches(":popover-open")));
    menu.onkeydown = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      menu.hidePopover();
      botao.focus({ preventScroll: true });
    };
    menu.showPopover();
    const posicao = botao.getBoundingClientRect();
    const acima = posicao.top - menu.offsetHeight - 5;
    const topo = acima >= 8 ? acima : Math.min(posicao.bottom + 5, innerHeight - menu.offsetHeight - 8);
    menu.style.top = Math.max(8, topo) + "px";
    menu.style.left = Math.max(8, Math.min(posicao.left, innerWidth - menu.offsetWidth - 8)) + "px";
    opcoes[0].focus({ preventScroll: true });
  }

  document.addEventListener("click", (e) => {
    const botao = e.target.closest("[data-copiar-resposta]");
    if (!botao) return;
    const tabelas = [...botao.closest(".resposta-chat").querySelectorAll("[data-tabela-copiavel] > table")];
    if (tabelas.length) abrirMenuCopia(botao, tabelas);
    else copiar(botao);
  });

  // Captura: roda antes do htmx ler o formulário.
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.matches("[data-refazer-resposta]") && window.htmx) {
      e.preventDefault(); e.stopImmediatePropagation();
      const compositor = document.querySelector("[data-compositor]");
      const campo = compositor?.querySelector("[data-campo]");
      if (atual || tarefaAtiva() || !campo || campo.disabled) return;
      const rascunho = campo.value;
      campo.value = "Executar de novo";
      compositor.querySelector("[data-refazer]").value = form.elements.refazer.value;
      compositor.requestSubmit();
      compositor.querySelector("[data-refazer]").value = "";
      if (atual?.form === compositor) atual.rascunho = rascunho;
      else campo.value = rascunho;
      return;
    }
    if (form.matches("[data-opcao-mensagem]")) {
      e.preventDefault(); e.stopImmediatePropagation();
      const compositor = document.querySelector("[data-compositor]");
      const campo = compositor && compositor.querySelector("[data-campo]");
      const opcao = form.querySelector('[name="mensagem"]');
      if (!atual && !tarefaAtiva() && campo && opcao && !campo.disabled) {
        campo.value = opcao.value;
        campo.dispatchEvent(new Event("input"));
        compositor.requestSubmit();
      }
      return;
    }
    const ocupado = tarefaAtiva();
    if (!atual && ocupado && ocupado.hasAttribute("data-objetivo-pendente") && form.matches("[data-compositor]")) {
      e.preventDefault(); e.stopImmediatePropagation();  // M50: chat ocupado; o campo está travado
      return;
    }
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
    const pedido = novoId();
    form.querySelector("[data-pedido]").value = pedido;
    atual = { form, texto, elementos: mostrarPensando(texto) };
    botao(form, true);
    acompanhar(form, pedido);
  }, true);

  document.body.addEventListener("htmx:beforeRequest", (e) => {
    // O htmx já leu os valores: pode limpar o campo.
    if (atual && e.detail.elt === atual.form) {
      const campo = atual.form.querySelector("[data-campo]");
      if (campo) {
        campo.value = ""; campo.dispatchEvent(new Event("input"));
        // M78: enquanto a IA trabalha, não dá para escrever; só o botão de parar fica ativo.
        if (!campo.dataset.placeholderOriginal) campo.dataset.placeholderOriginal = campo.placeholder;
        campo.disabled = true;
        campo.placeholder = "Aguarde a resposta ou clique em parar.";
      }
    }
  });
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach((ev) =>
    document.body.addEventListener(ev, (e) => {
      if (atual && e.detail.elt === atual.form) encerrar(true);
    }));
  document.body.addEventListener("htmx:afterRequest", (e) => {
    const xhr = e.detail && e.detail.xhr;
    const caminho = xhr && xhr.status >= 200 && xhr.status < 300 && xhr.getResponseHeader("HX-Push-Url");
    if (caminho) {
      const destino = new URL(caminho, location.href);
      if (destino.origin === location.origin && /^\/c\/\d+\/$/.test(destino.pathname)) {
        history.replaceState(null, "", destino.pathname);
      }
    }
    // 204 = envio interrompido no servidor: a tela já foi restaurada.
    if (atual && e.detail.elt === atual.form && e.detail.xhr && e.detail.xhr.status === 204) encerrar(true);
  });

  document.addEventListener("DOMContentLoaded", () => {
    atualizarSaudacao();
    prepararLateral();
    prepararCampo();
    atualizarBotaoTarefa();
    retomarEnvio();
    rolarParaFim();
    // M53: o cartão do pedido é trocado por outerHTML quando termina; o evento do htmx sai do elemento
    // já removido e não chega aqui. Observa a página para destravar o campo assim que o cartão some.
    let pendente = false;
    new MutationObserver(() => {
      if (pendente) return;
      pendente = true;
      requestAnimationFrame(() => { pendente = false; atualizarBotaoTarefa(); });
    }).observe(document.body, { childList: true, subtree: true });
  });
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) rolarParaFim();
  });
  // Anima apenas a opacidade, mantendo a altura da resposta durante a leitura.
  function animarResposta(msg) {
    if (!msg || msg.dataset.animada || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    msg.dataset.animada = "1";
    msg.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 180 });
  }
  let animarProxima = false;

  const leituraPorRequisicao = new WeakMap();
  document.body.addEventListener("htmx:beforeSwap", (e) => {
    const { target, xhr, shouldSwap } = e.detail;
    if (!shouldSwap || !xhr) return;
    const area = document.getElementById("conversa");
    if (target?.id === "conversa" && target !== area) {
      e.detail.shouldSwap = false;
      return;
    }
    const parte = target?.closest(".tarefa-card, .atividade-fixa");
    if (target === area || (parte && area?.contains(parte))) {
      leituraPorRequisicao.set(xhr, { vista: capturarLeitura(area), area, fragmento: target !== area });
    }
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    atualizarBotaoTarefa();
    const leitura = leituraPorRequisicao.get(e.detail?.xhr);
    if (leitura?.fragmento) {
      if (leitura.area.isConnected) restaurarLeitura(leitura.area, leitura.vista);
      return;
    }
    if (e.detail?.target?.id !== "conversa") return;
    if (animarProxima) {
      animarProxima = false;
      const ias = document.querySelectorAll(".mensagens .msg.ia:not(.pensando)");
      animarResposta(ias[ias.length - 1]);
    }
    const rascunho = atual?.rascunho;
    if (atual && !atual.form.isConnected) { atual = null; pararAcompanhamento(); }
    atualizarBotaoTarefa();
    atualizarSaudacao();
    prepararLateral();
    const area = document.getElementById("conversa");
    const vista = leitura?.vista;
    document.title = area.querySelector("h1").textContent + " · IAtiva";
    prepararCampo(vista?.conversa !== area?.dataset.conversaId);
    const campo = area?.querySelector("[data-campo]");
    if (campo && rascunho !== undefined && vista?.conversa === area.dataset.conversaId) {
      campo.value = rascunho;
      campo.dispatchEvent(new Event("input"));
    }
    retomarEnvio();
    if (!restaurarLeitura(area, vista)) rolarParaFim();
  });
  document.body.addEventListener("htmx:afterSettle", (e) => {
    const leitura = leituraPorRequisicao.get(e.detail?.xhr);
    if (!leitura) return;
    const area = document.getElementById("conversa");
    if (leitura.fragmento ? area === leitura.area : e.detail.target?.id === "conversa") {
      restaurarLeitura(area, leitura.vista);
    }
  });
  setInterval(atualizarSaudacao, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) atualizarSaudacao(); });
  window.matchMedia("(max-width: 680px)").addEventListener("change", () => app.classList.remove("menu-aberto"));

  let sincronizando = false, versaoRemota = "";
  async function sincronizar() {
    const area = document.getElementById("conversa");
    if (document.hidden || atual || sincronizando || document.body.classList.contains("redimensionando") ||
        document.querySelector(".renomeando") || !app.dataset.syncUrl) return;
    sincronizando = true;
    try {
      const qs = new URLSearchParams({ conversa: area?.dataset.conversaId || "0", versao: versaoRemota });
      if (area?.classList.contains("memoria-area")) qs.set("pagina", "memoria");
      const r = await fetch(app.dataset.syncUrl + "?" + qs, { credentials: "same-origin", cache: "no-store" });
      if (!r.ok || r.redirected || atual || area !== document.getElementById("conversa")) return;
      const dados = await r.json();
      if (atual || area !== document.getElementById("conversa")) return;
      const lateralOcupada = document.getElementById("lateral").contains(document.activeElement);
      if (dados.lateral && !lateralOcupada) {
        const doc = new DOMParser().parseFromString(dados.lateral, "text/html");
        const novo = doc.getElementById("lateral");
        const antigo = document.getElementById("lateral");
        const nav = antigo.querySelector("nav");
        const scroll = nav ? nav.scrollTop : 0;
        antigo.replaceWith(novo);
        if (window.htmx) htmx.process(novo);
        prepararLateral();
        if (novo.querySelector("nav")) novo.querySelector("nav").scrollTop = scroll;
      }
      if (dados.conversa && area) {
        const doc = new DOMParser().parseFromString(dados.conversa, "text/html");
        const recebida = doc.getElementById("conversa");
        const mensagens = area.querySelector(".mensagens"), novas = doc.querySelector(".mensagens");
        const vista = capturarLeitura(area);
        const mudou = !recebida.dataset.historicoVersao || recebida.dataset.historicoVersao !== area.dataset.historicoVersao;
        if (mudou && mensagens && novas) {
          const antes = mensagens.querySelectorAll(".msg.ia:not(.pensando)").length;
          mensagens.replaceWith(novas);
          const ias = novas.querySelectorAll(".msg.ia:not(.pensando)");
          if (ias.length > antes) animarResposta(ias[ias.length - 1]); // resposta do segundo plano
          if (!document.querySelector("[data-envio-pendente]")) pararAcompanhamento();
          retomarEnvio();
          area.dataset.historicoVersao = recebida.dataset.historicoVersao || "";
          if (window.htmx) htmx.process(novas);
          restaurarLeitura(area, vista);
        }
        area.querySelector("h1").textContent = doc.querySelector("h1").textContent;
        document.title = area.querySelector("h1").textContent + " · IAtiva";
      }
      versaoRemota = lateralOcupada ? "" : dados.versao;
    } catch (_) { /* indisponibilidade transitória: tentar no próximo ciclo */ }
    finally { sincronizando = false; }
  }
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail && e.detail.target && e.detail.target.id === "conversa") versaoRemota = "";
  });
  setInterval(sincronizar, 2000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) sincronizar(); });
})();
