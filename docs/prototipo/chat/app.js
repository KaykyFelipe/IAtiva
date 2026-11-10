// Protótipo: só troca o conteúdo fixo das conversas. Nada é enviado nem salvo.
const app = document.getElementById("app");
const mensagens = document.getElementById("mensagens");
const titulo = document.getElementById("titulo-chat");
const escopo = document.getElementById("escopo-chat");

const CHATS = {
  inadimplencia: ["Inadimplência em dezembro", "Conversa individual"],
  vencer: ["Títulos a vencer em janeiro", "Conversa individual"],
  cliente: ["Posição do cliente 000123", "Conversa individual"],
  setor: ["Cobrança", "Conversa do setor · 6 participantes"],
  mestre: ["Chat mestre", "Diretoria · setores liberados na sua política"],
  vazia: ["Nova conversa", "Conversa individual"],
};

function abrir(chave, link) {
  const modelo = document.getElementById("chat-" + chave);
  mensagens.replaceChildren(modelo.content.cloneNode(true));
  [titulo.textContent, escopo.textContent] = CHATS[chave];
  document.querySelectorAll(".conversa").forEach((c) => c.classList.toggle("ativa", c === link));
  app.classList.remove("menu-aberto");
  document.getElementById("rolagem").scrollTop = 0;
}

document.querySelectorAll(".conversa").forEach((link) =>
  link.addEventListener("click", (e) => {
    e.preventDefault();
    abrir(link.dataset.chat, link);
  })
);
document.getElementById("nova-conversa").addEventListener("click", () => abrir("vazia", null));
document.getElementById("abrir-lateral").addEventListener("click", () => app.classList.add("menu-aberto"));
document.getElementById("fechar-lateral").addEventListener("click", () => app.classList.remove("menu-aberto"));
document.getElementById("compositor").addEventListener("submit", (e) => e.preventDefault());

document.querySelectorAll("[data-papel-btn]").forEach((btn) =>
  btn.addEventListener("click", () => {
    const diretoria = btn.dataset.papelBtn === "diretoria";
    app.classList.toggle("diretoria", diretoria);
    document.querySelector(".perfil .quem span").textContent = diretoria ? "Diretoria" : "T.I";
    document.querySelectorAll("[data-papel-btn]").forEach((b) => b.classList.toggle("ativo", b === btn));
    if (!diretoria && document.querySelector('.conversa.ativa[data-chat="mestre"]')) {
      abrir("inadimplencia", document.querySelector('[data-chat="inadimplencia"]'));
    }
  })
);

const campo = document.getElementById("campo");
campo.addEventListener("input", () => {
  campo.style.height = "auto";
  campo.style.height = Math.min(campo.scrollHeight, 200) + "px";
});

abrir("inadimplencia", document.querySelector('[data-chat="inadimplencia"]'));
