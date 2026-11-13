# Mascote IAtiva (M46)

Identidade própria aprovada por Diego a partir da prévia de transformações em produtos da Ativa.
SVG local com corpo e olhos persistentes; CSS altera geometria e acessórios sem GIF pesado na aplicação.
A cápsula fica centralizada no cabeçalho e acompanha o mascote da mensagem em processamento.

| Estado | Visual |
|---|---|
| Pronta | Corpo arredondado, respiração e piscada |
| Pensando | Inclinação e pontos de pensamento |
| Buscando | Lupa em movimento |
| Consultando | Toalete portátil e olhos lendo |
| Organizando | Container habitável |
| Calculando | Calculadora |
| Preparando resposta | Prancheta |
| Conferindo | Capacete e marca de conferência |
| Concluído | Lavatório e sorriso, breve comemoração |
| Atenção | Cor âmbar e alerta |
| Interrompido | Movimento suspenso |

`static/js/mascote.js` expõe `IAtivaMascote` para os eventos de envio, andamento e HTMX.
Os textos operacionais já publicados pelo backend determinam o estado: não há tempo artificial
de consulta, chamadas adicionais ao modelo ou mudanças de permissão. Trabalho em segundo plano
impede conclusão visual prematura. Falhas de envio e validação ficam como atenção; parar fica interrompido.
Respostas antigas têm avatar estático para evitar centenas de animações simultâneas.
`prefers-reduced-motion` desativa animações e transições mantendo o desenho e o texto de estado.
O template clonável não tem IDs internos, scripts inline nem conteúdo de usuário interpolado.
