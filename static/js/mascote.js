(function () {
  let terminalTimer, repousoTimer, marcaTimer, emRepouso = false, proximaMarca = 0;
  const operacionais = new Set(['querying', 'organizing', 'calculating', 'writing', 'checking', 'searching']);
  function criar(estado = 'idle') {
    const t = document.getElementById('mascote-template');
    if (!t) return document.createElement('span');
    const el = t.content.firstElementChild.cloneNode(true);
    el.dataset.estado = operacionais.has(estado) ? 'thinking' : estado;
    return el;
  }
  function etapa(texto) {
    const t = (texto || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    if (/erro|falhou|indisponivel|nao conclu|sem permissao/.test(t)) return 'attention';
    return 'thinking';
  }
  const nomes = {idle:'Pronta para ajudar', thinking:'Pensando', searching:'Buscando', querying:'Consultando',
    calculating:'Calculando', organizing:'Organizando', writing:'Preparando resposta', checking:'Conferindo',
    done:'Concluído', attention:'Requer atenção', paused:'Interrompido'};
  function estado(valor) {
    clearTimeout(terminalTimer);
    if (operacionais.has(valor)) valor = 'thinking';
    if (valor !== 'idle') {
      emRepouso = false;
      clearTimeout(repousoTimer); clearTimeout(marcaTimer);
    } else if (!emRepouso) {
      emRepouso = true;
      repousoTimer = setTimeout(() => {
        if (!emRepouso || document.hidden || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { emRepouso = false; return; }
        const cabeca = document.querySelector('.agente-capsula [data-mascote]');
        if (cabeca) cabeca.dataset.estado = proximaMarca++ % 2 ? 'container' : 'cabine';
        marcaTimer = setTimeout(() => {
          if (cabeca) cabeca.dataset.estado = 'idle';
          emRepouso = false;
          estado('idle');
        }, 6000);
      }, 90000);
    }
    document.querySelectorAll('.agente-capsula [data-mascote], .pensando [data-mascote]').forEach(el => { if (!(valor === 'idle' && emRepouso && ['cabine', 'container'].includes(el.dataset.estado))) el.dataset.estado = valor; });
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
