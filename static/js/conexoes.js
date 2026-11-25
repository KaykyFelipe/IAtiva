function ajustarGemini(form) {
  const modelo = form.querySelector('[name="modelo"]');
  const esforco = form.querySelector('[name="esforco"]');
  const outro = form.querySelector('[data-gemini-outro]');
  const manual = modelo.value === 'outro';
  outro.hidden = !manual;
  outro.querySelector('input').required = manual;

  const permitidos = modelo.selectedOptions[0]?.dataset.esforcos?.split(' ') || [];
  for (const opcao of esforco.options) {
    opcao.disabled = Boolean(opcao.value) && !permitidos.includes(opcao.value);
  }
  if (esforco.selectedOptions[0]?.disabled) esforco.value = '';
}

function iniciarConexoes() {
  document.querySelectorAll('[data-gemini-modelo]').forEach(ajustarGemini);
}

document.addEventListener('change', (event) => {
  const form = event.target.closest('[data-gemini-modelo]');
  if (form && event.target.name === 'modelo') ajustarGemini(form);
});
document.addEventListener('htmx:afterSwap', iniciarConexoes);
iniciarConexoes();
