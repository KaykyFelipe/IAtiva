(function () {
  let terminalTimer;
  function criar(estado = 'idle') {
    const t = document.getElementById('mascote-template');
    if (!t) return document.createElement('span');
    const el = t.content.firstElementChild.cloneNode(true);
    el.dataset.estado = estado;
    return el;
  }
  function etapa(texto) {
    const t = (texto || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    if (/erro|falhou|indisponivel|nao conclu|sem permissao/.test(t)) return 'attention';
    if (/lendo o pedido|lendo o historico|decidindo|interpretando/.test(t)) return 'thinking';
    if (/calcul|somando|total no banco|percent|media/.test(t)) return 'calculating';
    if (/confer|valid|compar|verific|classific/.test(t)) return 'checking';
    if (/escrev|resposta final|guardando|memoria/.test(t)) return 'writing';
    if (/organiz|agrup|consolid|prepar/.test(t)) return 'organizing';
    if (/procur|localiz|dicionario|descobr|buscando|investig/.test(t)) return 'searching';
    if (/consult|erp|banco|lendo/.test(t)) return 'querying';
    return 'thinking';
  }
  const nomes = {idle:'Pronta para ajudar', thinking:'Pensando', searching:'Buscando', querying:'Consultando',
    calculating:'Calculando', organizing:'Organizando', writing:'Preparando resposta', checking:'Conferindo',
    done:'Concluído', attention:'Requer atenção', paused:'Interrompido'};
  function estado(valor) {
    clearTimeout(terminalTimer);
    document.querySelectorAll('.agente-capsula [data-mascote], .pensando [data-mascote]').forEach(el => el.dataset.estado = valor);
    const label = document.querySelector('[data-mascote-status]');
    if (label) label.textContent = nomes[valor] || nomes.idle;
  }
  function sincronizar(concluiu = false) {
    const pensando = document.querySelector('[data-pensando-atual]');
    const atividade = document.querySelector('[data-tarefa-ativa] .atividade-texto, [data-objetivo-pendente] .atividade-texto, .tarefa-card[hx-get] .tarefa-agora');
    if (pensando || atividade) { estado(etapa((pensando || atividade).textContent)); return; }
    const area = document.getElementById('conversa');
    if (area && area.dataset.falha === '1') { estado('attention'); return; }
    estado(concluiu ? 'done' : 'idle');
    if (concluiu) terminalTimer = setTimeout(() => estado('idle'), 2200);
  }
  window.IAtivaMascote = {criar, estado, etapa, sincronizar};
  document.addEventListener('DOMContentLoaded', () => sincronizar());
  document.body.addEventListener('htmx:afterSwap', e => {
    const id = e.detail?.target?.id;
    const terminou = id?.startsWith('tarefa-') && document.getElementById(id)?.classList.contains('concluida');
    if (id === 'conversa' || id === 'atividade-fixa' || id?.startsWith('objetivo-') || id?.startsWith('tarefa-')) sincronizar(id === 'conversa' || terminou);
  });
  ['htmx:responseError','htmx:sendError','htmx:timeout'].forEach(ev => document.body.addEventListener(ev, () => estado('attention')));
})();
