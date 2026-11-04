# Vale - Cadastro Facial e NFC (Windows)

Versão atual: `1.0.0-preview.44` (prévia técnica; ainda não homologada em catraca física).

Aplicativo Windows para check-in de visitantes diretamente no HikCentral Professional na rede interna. Sem sincronização online/offline e sem servidor intermediário.

## Operação
- Nome, sobrenome e grupos Visitor reais obtidos no HikCentral.
- Foto via webcam ou arquivo, enviada via Base64 JPEG. A webcam tem prévia ao vivo, captura e opção de refazer.
- Cadastro aceita reconhecimento facial, pulseira/cartão ou ambos; ao menos um método é obrigatório.
- Pulseira NFC ACR122U/PC-SC com número editável e conversão configurável: HEX, decimal big-endian, decimal little-endian (bytes invertidos), inversão de todos os bits e inversão dos bits de cada byte. Essas opções não são equivalentes; a correspondência real deve ser confirmada com um cartão cadastrado no HikCentral.
- Ingressos de 1, 2 ou 3 dias, personalizáveis. Por padrão a saída é às 23:59:59 do último dia incluído.
- Entrada e saída editáveis, com fuso America/Sao_Paulo. A saída é recalculada automaticamente ao alterar a entrada ou tipo de ingresso; não há botão de cálculo.
- Campo **Reserva ou Apartamento**, opcional, armazenado no VisitorInfo.remark.
- Ao clicar em CADASTRAR, o aplicativo cria a reserva e registra o check-in na mesma operação, após a confirmação da prévia de acesso.
- Níveis de acesso configurados por grupo. O padrão não concede níveis; o operador precisa definir a política aprovada.
- Antes de enviar, mostra os níveis e catracas que receberão a operação e pede confirmação.
- Consulta apenas os métodos enviados em cada catraca após solicitar a reaplicação.
- Recuperação automática: após falha, a aplicação guarda um checkpoint sem dados faciais; em nova conexão, consulta o registro de check-in sem gravar novamente e reaplica níveis pendentes. Se a consulta não conseguir provar um check-in único, interrompe a recuperação e mantém o cadastro bloqueado até conciliação segura. Não há botão manual.

Grupos iniciais conforme as imagens: Day use, Hóspedes Serra, Hóspedes Vale, Visitors. A lista real sempre é consultada antes da operação.

Níveis iniciais conforme as imagens: CATRACAS SABIA, ENTRADA ACQUAVALE, LOJA ACQUAVALE, SAIDA ACQUAVALE. Edite o mapeamento em Configurações conforme a política de acesso do hotel.

## OpenAPI (não usa login Web)
1. POST /artemis/api/visitor/v1/visitorgroups: recuperar grupos.
2. POST /artemis/api/acs/v1/privilege/group com type=2: consultar níveis Visitor e catracas associadas.
3. POST /artemis/api/visitor/v2/appointment: criar a reserva e receber appointRecordId e visitorId.
4. POST /artemis/api/visitor/v1/registerment: registrar o check-in e guardar o ID do registro separadamente.
5. POST /artemis/api/acs/v1/privilege/group/single/addPersons com type=2: atribuir níveis ao visitorId.
6. POST /artemis/api/visitor/v1/auth/reapplication: solicitar envio às catracas.
7. POST /artemis/api/visitor/v1/person/ID/elementDownloadDetail: consultar estado de face e pulseira por catraca.

## Instalação / compilação
No GitHub, entre em Actions > Compilar EXE Windows e faça download do artefato ValeCadastroFacialNFC-Windows depois de execução bem-sucedida. Ele contém ValeCadastroFacialNFC.exe compilado pelo PyInstaller com runtime Python embutido.

Requisitos externos: Windows 10/11, rede até o HikCentral e drivers PC/SC do leitor ACR122U (para leitura NFC). Câmera configurada no Windows.

Para executar pelo código: Python 3.13, pip install -r requirements.txt, python app.py.
Testes: python -m pytest -q tests.
Para validar a conexão e as dependências sem abrir a interface ou alterar visitantes, execute `ValeCadastroFacialNFC.exe --check-openapi <arquivo.json>`. O relatório traz endpoint, contagens, estado de importação da câmera/PC-SC e presença do serviço/leitor; não inclui credenciais nem dados de visitantes.

## Primeira configuração
1. No HikCentral Web Client, habilite o OpenAPI Gateway e crie um parceiro (AppKey/AppSecret) com permissões de Visitor e ACS. A conta admin do Web Client não funciona como AppKey.
2. Abra Configurações. O padrão deste servidor é 127.0.0.1:443. Confirme host e porta na instalação onde o aplicativo será usado.
3. Configure AppKey, AppSecret e, se necessário, caminho da CA PEM do certificado HTTPS. O aplicativo NÃO desativa a validação TLS/hostname. Para acesso por IP, o certificado precisa ter IP em SAN, ou utilize nome DNS correspondente.
4. Selecione conversão UID e valide o número real com uma pulseira de teste no HikCentral. O UID do ACR122U não é necessariamente igual à credencial configurada na catraca.
5. Defina explicitamente os níveis aprovados para cada grupo, ajuste os ingressos e salve. O programa sincroniza grupos/níveis automaticamente ao abrir e após salvar configurações, com tentativas de reconexão. Grupos sem níveis configurados não podem cadastrar.
6. Antes de cada cadastro, confira a prévia de níveis e catracas. Confirme também que a pulseira de teste não está vinculada a outro visitante; a consulta de duplicidade de cartão ainda não foi homologada.

## Segurança e persistência
Configurações ficam em %LOCALAPPDATA%\ValeCatracaSimples\settings.json; as chaves OpenAPI ficam em secrets.dpapi protegidas por DPAPI do usuário Windows. pending.json e last_delivery.json guardam apenas IDs necessários à recuperação e consulta, sem fotos, nomes ou números de cartão. Fotos não são gravadas no disco pela aplicação. Não inclua credenciais em arquivos do GitHub.

A senha admin compartilhada no planejamento NÃO é incorporada ao aplicativo e deve ser trocada. O uso de dados faciais depende de política de privacidade, base legal e segurança adequadas à LGPD.

## Limites da entrega
O servidor HikCentral, leitores e catracas precisam de homologação presencial. Resposta positiva na solicitação de reaplicação não comprova entrega a todos os dispositivos. Verifique status no HikCentral.

Referência: https://enpinfo.hikvision.com/hkwsen/unzip/20260723200832_95640_doc/index.html

## Certificado privado do HikCentral (localhost:443)

O Vale Visitor PHP anterior podia operar com TLS_VERIFY=0. Para evitar o envio
de credenciais e fotos para um serviço não autenticado, o aplicativo Windows
aceita duas alternativas de verificação:

1. Usar DNS correto no certificado + CA PEM confiável (opção preferencial).
2. Ler e conferir a impressão digital SHA-256 do certificado instalado no
   HikCentral, confirmando-a fora do aplicativo antes de aprová-la.

Na opção 2, o certificado TLS deve coincidir exatamente com o SHA-256 aprovado
ANTES de transmitir AppKey, AppSecret, nome, foto ou credencial NFC.
Qualquer substituição de certificado bloqueia a transmissão automaticamente.

A impressão deve ser comparada com um certificado obtido por canal confiável
no servidor; não confie nela só porque foi mostrada em conexão não validada.
Este modo não valida CA, hostname nem validade: use somente em ambiente interno
autorizado e servidor cuja identidade foi confirmada.

Use Diagnóstico de rede para saber se DNS, TCP e TLS estão acessíveis. Ao testar
a conexão, o aplicativo salva as alterações digitadas antes da chamada OpenAPI.
Não exponha senhas/chaves em prints nem no repositório GitHub.



## Alterações da preview.7

- Botão **Verificar última entrega** reposicionado ao lado de **CADASTRAR VISITANTE**, ambos no rodapé fixo da aba Cadastro.

## Alterações da preview.6

- Botão CADASTRAR VISITANTE fixado no rodapé da janela para não ser ocultado por controles longos ou resoluções menores.
- Sincronização e recuperação segura automáticas, sem botões de sincronizar ou concluir pendências na tela.
- Removidas dicas técnicas sob o leitor e botão Calcular saída.
- Datas enviadas como `YYYY-MM-DDTHH:MM:SS-03:00`, iguais ao fluxo PHP legado. A mesma data de saída vai em `appointEndTime` (reserva) e `visitEndTime` (check-in). Há testes automatizados cobrindo viradas de dia, mês e ano.
- **Importante:** não foi possível consultar o HikCentral do hotel deste ambiente. A data exibida na interface do HikCentral precisa ser confrontada com o payload real no servidor de recepção para confirmar se há conversão de fuso/representação do lado do HikCentral. Não altere o fuso arbitrariamente.
- Para NFC, a configuração antiga `DECIMAL_BIG` permanece se tiver sido salva anteriormente. O padrão de novas instalações é `DECIMAL_LITTLE`; revise o formato nas Configurações e confirme com uma pulseira conhecida. Não presuma equivalência entre inversão de bits, ordem dos bytes e formato Wiegand.

## Alterações da preview.8

- Grupos Visitor exibidos somente depois da consulta OpenAPI ao HikCentral; a lista interna serve apenas como referência inicial do sistema, sem popular o seletor antes da sincronização. Se a consulta falhar, novos cadastros continuam bloqueados.
- A aba Configurações exige senha a cada acesso. A senha fornecida pelo administrador é verificada por um hash PBKDF2-SHA256 com sal; **o projeto não armazena nem distribui a senha em texto puro**. Não há botão para mostrar senha nem recuperação no programa.
- Essa senha protege a interface contra alterações ocasionais na recepção, mas **não substitui permissões e proteção da conta Windows**; operadores com acesso ao código ou execução administrativa ainda podem modificar a aplicação.


## Alterações da preview.9

- O seletor Grupo Visitor usa apenas grupos reais retornados pela OpenAPI e oculta exclusivamente o grupo genérico `Visitors` (ignorando maiúsculas/minúsculas e espaços adicionais). Outros grupos cadastrados no HikCentral continuam disponíveis.
- Quando a API não retorna outro grupo ou falha, o campo é esvaziado e o cadastro é bloqueado. Não remove nem modifica o grupo `Visitors` no HikCentral.


## Alterações da preview.11 — Grupo preferencial por ponto de venda

- Na aba protegida **Configurações**, escolha **Grupo preferencial** em **Padrão deste ponto de venda**.
- O seletor permanece desabilitado até a sincronização dos grupos reais pela OpenAPI do HikCentral. Somente os grupos válidos são listados; `Visitors` permanece oculto.
- A opção **Sem preferência (primeiro disponível)** deixa a lista seguir a ordem retornada pelo servidor.
- Ao iniciar o programa e sincronizar, o grupo preferido salvo é apenas **pré-selecionado** no Cadastro, caso exista na API. Se tiver sido excluído no HikCentral, o sistema utiliza o primeiro grupo válido.
- O operador pode escolher outro grupo a qualquer momento. Uma nova sincronização não substitui a escolha manual se o grupo continuar válido.
- Depois de concluir o cadastro de um visitante, o formulário volta ao grupo preferencial para o próximo atendimento.
- A preferência é persistida em `%LOCALAPPDATA%\\ValeCatracaSimples\\settings.json`, chave `preferred_group`, por usuário Windows / computador de atendimento. Cada ponto deve configurar sua própria preferência.
- Se a API estiver indisponível, salvar outras configurações não cria grupos fictícios nem substitui a preferência que estava gravada.
- A preferência **não altera permissões ou a composição de grupos** no HikCentral; as permissões continuam sendo definidas pelo mapeamento de níveis por grupo.



## Interface moderna — preview.12

- Nova apresentação inspirada no mockup aprovado: navegação lateral escura, cabeçalho compacto, cartões claros com cantos arredondados, resumo de configuração, credenciais lado a lado e botão de cadastro destacado.
- Visual feito com CustomTkinter; os serviços e métodos de HikCentral, Artemis, NFC/ACR122U, webcam, TLS, cartões, datas, ingressos e recuperação permanecem no código já existente.
- "Visitors" continua oculto e só são exibidos grupos retornados pela OpenAPI; o grupo preferencial é apenas sugestão por ponto de venda.
- A aba Configurações segue protegida por senha. As ações Salvar, Testar conexão e Diagnóstico de rede permanecem fixas no rodapé da aba e não são encobertas por campos longos.
- O aplicativo possui rolagem vertical e suporta janelas menores, preservando as ações de cadastro.
- A tela é uma implementação nativa Windows, fiel à linguagem visual do mockup; diferenças menores de renderização podem ocorrer conforme escala de tela e tema do Windows.
- Nenhuma credencial deve ser publicada no repositório. A confirmação da operação real com catracas continua dependendo de teste local no hotel.


## Alterações da preview.14

- **Cadastro com somente um seletor de grupo Visitor**, sem cartões ou botões duplicados.
- **Grupo preferencial obrigatório por ponto de venda**, quando salvo em Configurações: após consultar a lista de grupos reais do HikCentral, o seletor Cadastro permanece bloqueado no grupo configurado. A seleção é novamente validada antes de cadastrar; se o grupo não existir mais na OpenAPI, o aplicativo bloqueia novas vendas até atualizar a preferência.
- Quando selecionado **Sem preferência**, o operador continua podendo escolher livremente qualquer grupo elegível da OpenAPI.
- Coluna direita de Resumo de configuração e Dica removida; o formulário ocupa toda a área à direita do menu.
- **Janela responsiva**: dimensão inicial baseada na tela e reserva de área da barra de tarefas do Windows. Se o cliente usa resolução menor ou escala maior, os formulários passam a uma coluna, com rolagem e botões Cadastro/Verificar última entrega e Salvar/Testar/Diagnosticar fixos e acessíveis.
- O serviço HikCentral, NFC, webcam, datas, ingressos, recuperação de cadastros, permissões e armazenamento de segredos não tiveram seus protocolos alterados.

A compatibilidade visual e resolução é testada automaticamente com geometria dinâmica; a validação funcional completa exige executar o EXE nos computadores reais do hotel.


## Preview.15 — Foto e funcionamento diário dos ingressos

- A prévia facial agora usa a área inteira do quadro (crop proporcional central com PIL ImageOps.fit); não modifica a foto enviada ao HikCentral.
- Ingressos agora usam Nome;Dias;Entrada;Saída, por exemplo:
  - 1 dia;1;09:00;17:00
  - 2 dias;2;09:00;17:00
  - 3 dias;3;09:00;17:00
- Em instalações novas, abertura 09:00 e encerramento 17:00, inclusive último dia. Um ingresso de 3 dias com início em 01/11 às 09:00 vence em 03/11 às 17:00.
- Migração conservadora: ingressos antigos de 3 campos preservam horário anterior de saída (ex.: 23:59) e recebem abertura padrão 09:00. Corrija manualmente e clique Salvar; não alteramos regra operacional silenciosamente.
- **Importante:** data de entrada no primeiro dia e saída no último NÃO impede acesso noturno entre os dias. É necessário configurar Access Schedule Template 09:00–17:00 para TODOS os dias e feriados em cada nível Visitor efetivamente usado, aplicar nos terminais e testar acesso antes/depois do horário.
- Configurações inclui campo Agendas diárias verificadas, formato Nome do Nível=09:00-17:00. Preencha SOMENTE depois de conferir o modelo de agenda nos níveis e dispositivos. É confirmação administrativa, não inspeção automatizada de configuração do HikCentral.
- O programa bloqueia o cadastro quando os níveis selecionados não têm confirmação para os horários configurados; horários diferentes exigem níveis diferentes.
- Não restrinja saídas de emergência nem rotas de evacuação; os horários operacionais não substituem as medidas de segurança.
- Testes automatizados cobrem multi-dia, fuso horário, migração, bloqueio sem agenda confirmada e proporção da foto.

## Histórico: política fixa da preview.16 (substituída na preview.17)

**Importante:** este bloco descreve a Preview 16 e não é mais a política operacional atual. Na Preview 17, os níveis são definidos por grupo.

**Política de acesso fixa:** em cada cadastro, independentemente do grupo Visitor selecionado, a aplicação consulta os níveis com `type=2` e exige exatamente um nível `VISITANTE`. O nível precisa retornar **10 IDs distintos de pontos** pela OpenAPI; divergências interrompem o cadastro antes de qualquer escrita. Os 10 IDs são reutilizados para a solicitação de distribuição, sem adicionar ou modificar pontos no HikCentral.

O aplicativo envia explicitamente `VISITANTE` em `accessInfo.accessLevelList` tanto na reserva (`/artemis/api/visitor/v2/appointment`) quanto no check-in (`/artemis/api/visitor/v1/registerment`). Após registro, a atribuição de nível usa `/artemis/api/acs/v1/privilege/group/single/addPersons` com `type=2` e o mesmo ID; a reaplicação usa os dez pontos retornados. Nenhuma chamada a `/artemis/api/resource/v1/person/single/add` ou departamento ACQUAVALE é feita. Os antigos mapeamentos `ENTRADA ACQUAVALE`, `SAIDA ACQUAVALE`, etc. permanecem armazenados apenas para compatibilidade e não são utilizados no cadastro. A tela Configurações os apresenta como legados, sem edição.

**Validade e horário:** o app transmite a mesma validade local `America/Sao_Paulo` no formato ISO 8601 com offset (`-03:00` na data testada) para `appointStartTime`, `appointEndTime`, `visitStartTime`, `visitEndTime`. O horário ACQUAVALE 09:00–17:00 todos os dias pertence ao nível no HikCentral; não é modificado pelo aplicativo. Antes de cadastrar, a recepção precisa ter a confirmação administrativa `VISITANTE=09:00-17:00` em Agendas diárias verificadas. Esta confirmação NÃO substitui uma consulta real de schedule nem um teste de catraca.

**Reexecuções:** antes de enviar a primeira reserva, `pending.json` registra um marcador sem nome, foto, número de cartão ou segredos. Caso a resposta da criação da reserva se perca, nenhum novo envio é feito automaticamente; é necessária conciliação manual no HikCentral antes de remover o bloqueio. Pendências legadas cujos níveis/pontos não coincidam com o único `VISITANTE` e seus dez pontos são bloqueadas, sem reaplicação. Falhas de API não apresentam a mensagem bruta do servidor, que pode conter PII ou credenciais.

**Conferência segura no servidor:** com o executável, rode `ValeCadastroFacialNFC.exe --check-openapi relatorio.json` usando a rede e as credenciais já protegidas no Windows. Este comando só consulta grupos e níveis, valida presença do nível `VISITANTE` com dez pontos e checa periféricos; não cria visitante, não distribui credenciais e não abre catraca. O relatório mostra `required_visitor_level`, `required_visitor_point_count` e sinaliza `effective_schedule_readback=not_verified_by_this_API_check` porque a consulta não prova a agenda; a versão e o template efetivos do HikCentral precisam ser conferidos diretamente no servidor.

**Limites da validação:** testes automatizados e respostas simuladas comprovam quais URLs/payloads seriam enviados e verificam a assinatura da solicitação, validade e política de acesso, mas não comprovam que o HikCentral real aceitou e persistiu um cadastro, nem que os dez terminais aplicaram as regras. Sem autorização específica, nenhum cadastro real foi criado. Documentação pública consultada: HikCentral Professional OpenAPI (publicação 2024) e fluxo PHP `integracao_catracas_hikivision`; confirmar a documentação da versão de HikCentral instalada antes de qualquer mudança nos endpoints.

## Preview.17 — Vínculo de níveis por grupo e diagnóstico de check-in

- Foi restaurado o campo editável **Níveis de acesso por grupo Visitor** na aba Configurações.
- Sintaxe: `Nome exato do grupo=Nome exato do nível Visitor`, uma linha por grupo. Exemplo de configuração de teste: `Hóspedes Vale=VISITANTE`.
- Cada grupo exige exatamente **um** nível; o aplicativo recusa grupos sem vínculo ou com vários níveis. O nível deve existir na consulta OpenAPI **type=2**, que exclui níveis de funcionários type=1. O aplicativo não cria nem altera níveis, departamentos ou pontos no HikCentral.
- O nível `VISITANTE` conserva a validação específica de **10 pontos**; outros níveis Visitor podem ter suas quantidades próprias, retornadas pela API, sem assumir que todos possuem dez.
- Cada nível utilizado deve ter sua **agenda diária efetiva** confirmada no HikCentral. Registre em Configurações `Nome do nível=09:00-17:00` somente após verificar a agenda aplicada.
- As associações realizadas na reserva, no check-in e na atribuição de privilégios seguem o mapeamento do grupo escolhido. Um checkpoint de recuperação só permite reaplicar o nível Visitor original comprovado, e nunca outro vínculo assumido.
- **Verificar última entrega** agora consulta separadamente a entrega aos dispositivos e, se a versão do cadastro deixou datas salvas, o registro de check-in via API somente leitura. Entrega confirmada não é considerada prova de check-in.
- Em cadastros feitos por versões anteriores, o arquivo local de última entrega pode não ter datas suficientes para essa segunda consulta. Nesse caso, verificar manualmente em **Visitor > Visitor Check-In/Out** e **Visitor Information**, com filtros de data e grupo.
- Um resultado incerto da reserva deixa um checkpoint local e bloqueia repetição automática para evitar duplicidade. Não remova o arquivo pendente sem conferir o registro real.
- O endereço/versão da API instalada e a persistência real de um visitante só poderão ser confirmados com consulta ao HikCentral do hotel; testes automatizados usam respostas simuladas. Não foi criado visitante novo durante esta correção.


## Preview.18 — VALID TIMES configurável

- Adicionado em **Configurações → Ponto de venda** o campo numérico **VALID TIMES**, com valor inicial **0**.
- **0** é enviado como `visitorInfoList[0].VisitorInfo.accessInfo.qrCodeValidNum = 0` nas chamadas de reserva e check-in. Conforme a OpenAPI do HikCentral, 0 é o valor padrão, enquanto 1 a 100 configura um número máximo de utilizações de QR Code.
- **1 a 100**: o valor informado é enviado ao HikCentral no mesmo campo, sem alterar a validade do ingresso nem a agenda do nível Visitor. Números negativos, não inteiros e acima de 100 são rejeitados.
- Configuração persistente por usuário Windows em `settings.json` (chave `valid_times`). Instalações antigas migrarão para 0 sem modificar demais preferências ou os ingressos existentes.
- A configuração só afeta **novos cadastros**; não edita automaticamente os visitantes já registrados no HikCentral.
- **Limitação importante:** a documentação do parâmetro descreve limite de autenticações por **QR Code**. O efeito do valor 0 sobre **cartão NFC e reconhecimento facial** não está garantido apenas pelo payload. É necessário conferir um visitante cadastrado com o novo EXE e testar mais de uma passagem, além de examinar os registros de acesso, antes de homologar.
- Testes unitários e mocks verificam configuração salva, limites válidos, payload de reserva e check-in com `qrCodeValidNum` igual ao escolhido. Não são criados visitantes reais nos testes.
- Referência documental: **HikCentral Professional OpenAPI Developer Guide V3.0.1**, Appendix A.1.1 (`accessInfo.qrCodeValidNum`: 0 por padrão, 1–100 para limitar aberturas por QR).

## Preview.19 — Níveis de acesso finais do AcquaVale

Atualização a partir da configuração de Access Control exibida pelo hotel em 02/11/2026:

| Grupo Visitor (seleção no cadastro) | Nível Visitor exato | Access Schedule | Quantidade esperada de pontos |
| --- | --- | --- | ---: |
| Day use | `DAY_USE` | `ACQUAVALE` | 8 |
| Hóspedes Serra | `VISITANTE_SERRA` | `ACQUAVALE` | 8 |
| Hóspedes Vale | `VISITANTE_VALE` | `ACQUAVALE` | 3 |

- Os níveis `REFEITORIO`, `DESENVOLVIMENTO`, `LOJA ACQUAVALE`, `CATRACAS SABIA`, `SAIDA ACQUAVALE` e `ENTRADA ACQUAVALE` usam All-Day e **não são associados automaticamente a visitantes**.
- A lista de grupos Visitor continua vindo exclusivamente da OpenAPI. O aplicativo não cria grupos nem modifica níveis, pontos de acesso, agenda ou funcionários.
- O operador ainda pode modificar **Níveis de acesso por grupo Visitor** nas Configurações; a nova correspondência é o padrão aprovado. Cada grupo continua exigindo um único nível type=2 validado na consulta. Níveis alternativos customizados só são aceitos se já existirem como níveis Visitor na API.
- Os pontos associados ao nível são lidos da OpenAPI; para os três níveis aprovados, o aplicativo recusa novos cadastros se a quantidade retornada for diferente de **8/8/3**. O número mostrado na página não substitui a checagem dos IDs retornados pela API.
- Os três níveis usam agenda `ACQUAVALE`, previamente informada como **09:00–17:00 todos os dias**. O aplicativo registra janelas diárias `DAY_USE=09:00-17:00`, `VISITANTE_SERRA=09:00-17:00`, `VISITANTE_VALE=09:00-17:00` em novos perfis. Não edita o schedule real no HikCentral.
- **Migração de estações existentes:** configurações sem revisão de mapeamento recebem a nova relação desses três grupos, preservando `host`, porta, certificados, `VALID TIMES`, formatos de NFC, foto, ingressos e preferências. Outros grupos não recebem permissões automaticamente. Na primeira gravação, é gerado backup de `settings.json` em `settings.backup-pre-visitor-levels.json`, no mesmo diretório local. A revisão é salva para permitir edições futuras sem substituição automática.
- Verifique em **Configurações > Níveis de acesso por grupo** e clique **Salvar configurações** após instalar para persistir a migração. Os testes automatizados são simulados e **não criam visitantes reais ou liberam catracas**. Para homologar, execute a consulta de OpenAPI e confira os três níveis e pontos na rede do hotel.

**Atenção:** as seções históricas Preview.16/17 usam o nível de teste `VISITANTE` e não refletem a organização de acesso aprovada na Preview.19.

## Preview.20 — Correção crítica de inicialização do Windows

- A Preview 19 não iniciava devido ao import de `VISITOR_LEVEL_NAME` em `app.py`, removido do `core.py` na atualização dos níveis Visitor. O import obsoleto foi retirado, sem restaurar o nível antigo como padrão.
- O novo EXE mantém a configuração aprovada: Day use → `DAY_USE` (8 pontos), Hóspedes Serra → `VISITANTE_SERRA` (8), Hóspedes Vale → `VISITANTE_VALE` (3); todos com a janela confirmada de 09:00–17:00.
- **Proteção contra regressão:** o pipeline verifica explicitamente `import app` e dependências, executa os testes, compila o EXE e, **antes de publicar o Release**, executa o próprio `ValeCadastroFacialNFC.exe --self-test <arquivo.json>` no Windows.
- O self-test utiliza configurações artificiais, verifica os níveis padrão e a quantidade de pontos, inicializa e encerra a interface Tk, não consulta rede, não lê credenciais reais e não cria visitantes.
- O GitHub Actions recusa publicar se o EXE não abrir, se o teste offline falhar ou se a versão embutida não corresponder ao código-fonte.
- A compilação e o smoke test não substituem a homologação dos recursos de câmera, leitor NFC e OpenAPI no hotel.

## Preview.21 — Indicador animado durante o cadastro no HikCentral

- O botão **CADASTRAR VISITANTE** continua fazendo a pré-validação (níveis Visitor e catracas) e exibindo o diálogo de confirmação. **Depois de clicar SIM**, aparece um cartão central com um **indicador circular girando**, o texto `Cadastrando visitante...` e `Aguardando resposta do HikCentral.`.
- Enquanto a solicitação de cadastro, check-in, distribuição e consulta de entrega estiver em andamento, os botões de cadastrar, consultar última entrega e navegação permanecem desabilitados para evitar solicitações repetidas. O worker de OpenAPI continua em thread separada; a interface Tk mantém animação fluida.
- Quando a API concluir, **com sucesso ou erro**, o indicador para, o cartão desaparece, os botões voltam a ficar disponíveis e a mensagem normal de resultado é mostrada. As proteções existentes de operação pendente e duplicidade continuam em vigor.
- Não há alteração de payloads, níveis, pontos de acesso, configuração de ingressos, fotos, NFC, nem horários do HikCentral.
- Testes automatizados no Windows simulam espera, animação e finalização nos caminhos de sucesso e falha e verificam que a prévia de níveis não exibe spinner.

## Preview.22 — Loading visível durante o cadastro

O indicador giratório passou a ser exibido em uma janela de progresso própria, vinculada à janela principal e posicionada ao centro, por cima do formulário. Isso evita que o ttk.Notebook oculte o indicador. O loading fica visível por pelo menos 700 ms, inclusive em respostas rápidas, continua ativo durante as chamadas reais ao HikCentral e desaparece antes da mensagem de sucesso ou erro. Botões de cadastro e navegação permanecem desabilitados enquanto a solicitação estiver em andamento. Foram incluídos testes Windows que confirmam a visibilidade da janela e seu encerramento ao terminar a operação. Não houve mudanças em cadastro Visitor, níveis ou NFC.

## Preview.23 — Cadastros efetuados

- Terceira aba no menu lateral: **Cadastros efetuados**, com lista de cadastros realizados **por este computador e usuário Windows**. Instalações anteriores não armazenavam histórico acumulado; cadastros antigos só poderão ser consultados pelos registros do próprio HikCentral.
- Armazena metadados do visitante (nome, grupo, reserva, Visitor ID, níveis, IDs das catracas, validade, resultado de check-in e estado da entrega), criptografados localmente por **Windows DPAPI** em `%LOCALAPPDATA%\\ValeCatracaSimples\\visitor_history.dpapi`. O arquivo exige o mesmo perfil Windows. **Não armazena foto facial, número do cartão ou AppSecret**.
- **Consultar catracas:** realiza apenas `getVistorRegisterRecord` (conciliação de check-in) e `elementDownloadDetail` (detalhamento da entrega), sem criar registros ou alterar dispositivos.
- **Reenviar credencial:** exige confirmação do operador, ausência de operação pendente, check-in localizado e níveis/pontos idênticos aos do cadastro original; usa apenas o endpoint existente de `auth/reapplication` com o **mesmo Visitor ID**, sem criar reserva, pessoa, novo cartão ou substituir níveis. Resultado de entrega é reconsultado.
- **Ver dados / corrigir:** mostra os dados originais salvos. A edição remota de nome, foto, NFC, validade e outros campos **não foi implementada** porque a OpenAPI de alteração de visitantes existente no servidor ainda não foi validada; o operador deve corrigir no HikCentral e então usar o reenvio no aplicativo. Isto evita cadastrar a pessoa novamente por engano.
- O histórico é atualizado por ID, nunca cria duplicatas do mesmo registro local. Não é compartilhado entre computadores. A aplicação antiga continua funcionando, incluindo cadastro Visitor, câmera/NFC, ingressos, grupos, spinner e configurações.
- Antes de distribuir em produção, verifique na rede local o retorno de cada catraca com um cadastro previamente autorizado; não foram criados visitantes reais durante os testes.

## Preview.24 — Edição de cadastros existentes

- Duplo clique numa linha de **Cadastros efetuados**, ou o botão **Ver dados / corrigir**, abre janela editável com nome, sobrenome, reserva, datas e opção de fornecer novamente foto e pulseira.
- O histórico local DPAPI não conserva foto nem NFC. Credenciais em uso devem ser fornecidas de novo para impedir apagamento silencioso durante atualização.
- A atualização usa `POST /artemis/api/visitor/v1/registerment/update` com o ID de check-in existente verificado pela leitura e níveis Visitor originais. **Não chama o endpoint de criar reserva ou de cadastrar novo visitante**.
- Valida igualdade dos pontos originais antes de editar. Depois usa o endpoint existente de reaplicação e consulta entrega nas catracas.
- Persiste `visitor_edit_inflight.json` antes da escrita remota para bloquear uma segunda tentativa em caso de falha incerta; operação incerta exige conciliação manual antes de nova tentativa. **A API do HikCentral e a interpretação do ID devolvido precisam ser homologadas em campo.**

## Preview.25 — Captura facial inteligente automática (offline)

- **Foto pela webcam** agora abre câmera com oval de referência e orientações em português: posicione, aproxime, afaste, centralize, mantenha a cabeça reta, melhore a iluminação, ajuste o foco, uma pessoa por vez.
- Reutiliza o critério do projeto `wfuzatto/face_scanner`: área facial relativa **0,105–0,48**, centralização **±0,16/±0,20**, iluminação **35–225**, nitidez **variância do Laplaciano ≥45**, estabilidade de **900 ms** e contagem **3–2–1**. Valores adaptáveis nas configurações. Quando possível, analisa a inclinação dos olhos usando o detector Haar local.
- Reconhecimento **apenas de presença, posição e qualidade**, não identifica nem compara rostos. Implementação inteiramente local com OpenCV Haar. Nenhum serviço externo, vídeo ou imagem temporária salva em disco.
- Webcam lida e analisada em **thread**; visualização do Tk continua responsiva. Captura JPEG de alta qualidade pela resolução suportada pela câmera, sem aplicar filtros estéticos ou deformar proporções.
- Quando o rosto está adequado e imóvel, inicia contagem e congela o frame automaticamente. Interrupções de enquadramento, luz, foco ou movimento reiniciam a avaliação. O visitante pode **Tirar novamente**, **Capturar manualmente**, **Usar esta foto** ou **Cancelar**.
- **A foto só é aplicada ao formulário após Usar esta foto**. A API HikCentral não é chamada pela câmera; a operação de registro continua no botão Cadastrar Visitante.
- **Configurações > Captura facial inteligente**: ativação (padrão ligado), estabilidade (400–3000 ms), sensibilidade, exigir qualidade e tempo para oferecer ajuda manual; mantém o índice da webcam existente e defaults em perfis antigos.
- Se o detector Haar não carregar ou não houver qualidade suficiente, a captura manual e a opção de selecionar um arquivo permanecem disponíveis. A webcam precisa estar acessível para obter frames.
- Pipeline Windows valida importação, detector XML offline incluído no PyInstaller, testes de avaliação e máquina de estados, e abertura do EXE sem rede; **não é homologação com webcam real da recepção**.
- Não modifica NFC, Visitor, agendas ACQUAVALE, níveis, histórico, reenvio, edição, VALID TIMES nem integração HikCentral.

## Preview.26 — Detector alinhado ao Face Scanner e orientações legíveis

- Corrigido o problema de detecção da Preview 25: ela usava **Haar**, ao passo que `wfuzatto/face_scanner` usa **OpenCV YuNet**. Agora ambos usam o mesmo detector YuNet 2023mar, `FaceDetectorYN`, threshold de detecção 0,85 e landmarks dos olhos para avaliar inclinação. Continua sem identificação biométrica e sem serviço externo no hotel.
- O modelo é obtido **somente durante o build** a partir do OpenCV Zoo, fixado no commit `47534e27c9851bb1128ccc0102f1145e27f23f98`, validado por tamanho 232589 bytes e SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`; o ONNX e sua licença MIT ficam embutidos no EXE. **Não há download na recepção**.
- Orientações de captura ficam em faixa de alto contraste, texto branco em fonte de 25px, mensagem maior e fundo que muda de cor quando o rosto está posicionado ou durante a contagem.
- Critérios de proporção, nitidez, luz, centralização e estabilidade mantidos e ajustáveis nas Configurações. Em falha de detector, uma mensagem explícita orienta a usar **Capturar manualmente**; não há fallback silencioso ao Haar antigo.
- Testes de regressão, critérios faciais, importação Windows e inferência de YuNet no EXE compilado. **Não equivale a homologação da webcam física da recepção**; conferir com a webcam e iluminação do local.
- Sem alterações no envio de cadastro, NFC, Visitor, edição, grupos, acessos ou integrações HikCentral.

## Preview.27 — Câmera na edição de foto de visitante

- Na janela **Editar cadastro**, o botão **Abrir câmera** fica ao lado de **Selecionar foto**, usando a mesma captura facial inteligente offline já existente no cadastro.
- **Usar esta foto** preenche apenas a nova foto do registro em edição. **Cancelar** mantém o estado anterior; a foto só é enviada quando o operador clicar em **Salvar e reenviar ao HikCentral**.
- A câmera respeita o índice e as preferências existentes, sem alterar as configurações, o NFC, datas, níveis de acesso, Visitor ID, endpoints ou fluxo de novos cadastros. A foto não é persistida no histórico.
- A janela de captura assume temporariamente o foco modal e devolve o controle à edição quando é fechada.
- A validação automatizada não substitui teste com a câmera física da estação.


## Preview.28 — Cadastros compartilhados entre recepções

- **Cadastros efetuados** agora consulta o HikCentral via `POST /artemis/api/visitor/v1/register/getVistorRegisterRecord` na rede interna. Qualquer estação com acesso OpenAPI autorizado pode pesquisar registros feitos em outros computadores.
- Filtros por nome e período (até 32 dias por consulta), navegação paginada, status de check-in e atualização manual; não cria registros.
- **Ver foto (HikCentral)** usa `POST /artemis/api/visitor/v1/appointment/downloadpicture`, com referência da foto fornecida pelo servidor. A imagem é carregada somente quando solicitada e nunca é gravada no histórico local.
- **Ver dados / corrigir** abre apenas detalhes de leitura quando o registro veio de outra estação. As ações de edição e reenvio ficam bloqueadas sem prova de IDs, pontos e níveis originais; não são inferidos a partir das configurações atuais. Registros do próprio computador preservam suas funções anteriores quando os IDs e o período conferem.
- Quando o HikCentral falhar, o aplicativo informa claramente que exibe **somente dados locais**, sem apresentá-los como consulta central.
- Continua usando a OpenAPI existente e validação TLS. Não altera NFC, regras de grupo, agendas, validade, payload de cadastro, leitor, captura facial nem criação de visitantes.
- A pesquisa central e o retorno de fotos dependem dos endpoints e permissões efetivamente disponíveis no HikCentral instalado. Testes automatizados são simulados e não comprovam que o servidor ou catracas do hotel respondem aos novos endpoints.


## Preview.29 — Horário atualizado a cada novo cadastro

- Corrigido o congelamento da hora de Entrada entre atendimentos: depois de concluir um cadastro, o programa atualiza a hora de início usando o relógio local e recalcula a saída conforme o tipo de ingresso e sua janela autorizada.
- Se o formulário permanecer aberto, antes de iniciar outro cadastro o horário é atualizado somente quando o operador **não alterou manualmente** a entrada ou saída. Datas ajustadas pelo atendente são preservadas.
- O histórico passa a registrar `registered_at` (instante real do sucesso do cadastro, com fuso horário) separadamente de `entry_at` (início da validade do ingresso), preservando o instante do primeiro registro nas atualizações posteriores.
- Em **Cadastros efetuados**, os campos **Cadastrado em** e **Início válido** ficam em colunas distintas. Registros antigos sem `registered_at` exibem “—” em vez de um horário estimado.
- Sem alterações nos grupos, níveis, emissão NFC, câmeras, biometria, cálculo dos dias ou endereços do HikCentral.
- Regressões automatizadas para cadastros sucessivos, renovação de período e respeito a datas ajustadas manualmente.



## Preview.30 — VALID TIMES fixo em zero; persistência ao reabrir

- Para **novos visitantes Day use, Hóspedes Serra e Hóspedes Vale**, o campo OpenAPI `visitorInfoList[0].VisitorInfo.accessInfo.qrCodeValidNum` é **sempre 0**, tanto na reserva quanto no check-in, independentemente de configurações antigas (1–100). A edição de visitante existente também aplica 0 caso o operador confirme essa atualização.
- **Configurações → VALID TIMES** agora exibe `0` em campo somente leitura. O operador não consegue ativar acidentalmente uma contagem de utilizações; as datas de validade e agendas dos níveis permanecem inalteradas.
- Na inicialização, `load_settings()` lê `%LOCALAPPDATA%\\ValeCatracaSimples\\settings.json` e, se detectar valor anterior diferente de zero, cria uma cópia única `settings.backup-pre-valid-times-zero.json` e corrige **apenas a chave `valid_times`** no JSON. Nome do servidor, porta, índice da câmera, NFC, grupos, ingressos e demais opções permanecem intactos. A gravação manual também força 0.
- `secrets.dpapi` contém AppKey/AppSecret protegidos pelo perfil Windows, recarregados na abertura. `visitor_history.dpapi` preserva o histórico local sem guardar fotos ou números de cartão. A lista compartilhada consulta o HikCentral após conectar; não existe cópia central das configurações locais entre computadores.
- **Cadastros anteriores no HikCentral com VALID TIMES = 1 NÃO são corrigidos automaticamente.** O supervisor deve revisá-los na interface do HikCentral; o EXE não altera pessoas antigas sem operação de edição deliberada.
- A documentação do parâmetro fala de quantidade de autenticações por QR Code; o comportamento dos terminais facial/NFC e a apresentação do toggle no Web Client exigem verificação no servidor real.
- Testes automatizados cobrem o envio de zero em reserva/check-in, migração de `settings.json`, recarga após reiniciar e preservação das demais preferências. Os testes não consultam visitantes reais.


## Preview.31 — Supervisão central, registro real, níveis e foto antes de editar

- A lista de check-ins agora lê `registerTime` da própria OpenAPI, exibe dia/hora/segundos no campo **Cadastrado em**, e mantém `visitStartTime` separado em **Início válido**. Não inventa datas para registros antigos: o mesmo `visitStartTime` pode estar repetido por um problema da versão anterior, mas `registerTime` é o horário real salvo pelo HikCentral.
- O nível de acesso agora é obtido da consulta de **reservas** `appointment/appointmentlist`, em `visitorInfo.accessInfo.accessLevelList`; só é exibido quando a reserva corresponde ao **mesmo Visitor ID, entrada e saída**. Em caso de erro/ausência, mostra **não informado**, nunca atribui automaticamente pelo grupo.
- **Ver dados / corrigir** abre a janela de detalhes para todos os visitantes consultados, inclusive de outra estação. A foto atual é solicitada à OpenAPI `appointment/downloadpicture` e exibida no topo; é mantida apenas em memória. O operador pode substituí-la por arquivo ou câmera quando há autorização para editar.
- Para editar cadastro remoto, o aplicativo valida por leitura o **recordId do check-in**, localiza uma **reserva única da mesma pessoa e período**, confirma o **ID do nível Visitor type=2** e os pontos de acesso. A edição somente habilita `Salvar` quando essas provas e as credenciais originais (foto/NFC, conforme aplicável) estão disponíveis; caso contrário, abre a mesma janela **somente leitura**, exibindo o motivo.
- Nunca cria visitante ou reserva durante a consulta. A ação de salvar continua exigindo confirmação e executa `registerment/update` no ID existente, sem retentativas automáticas de gravação. As configurações, as regras de validação do ingresso, VALID TIMES fixo 0, câmera, grupo e NFC não foram alteradas.
- Testes automatizados usam respostas simuladas: incluem `registerTime` real, seleção de reservas por identidade e período, validação dos IDs e recusa de edição insegura. A homologação dos fluxos de alteração depende das respostas reais do HikCentral no hotel.

## Preview.32 — Término da validade, primeira entrada e última saída

- Aba **Cadastros efetuados**: colunas **Cadastrado em**, **Início válido**, **Término válido**, **Visitante**, **Grupo**, **Check-in**, **Primeira entrada** e **Última saída**. Foram removidas as colunas **Nível** e **Catracas**. Há rolagem horizontal para monitores menores.
- **Término válido** utiliza somente `visitEndTime` retornado pela OpenAPI, em horário local; não significa saída efetiva.
- **Primeira entrada** e **Última saída** consultam `POST /artemis/api/acs/v1/door/events`, com filtro por `personId` exato e intervalo daquela visita. Apenas eventos **Access Granted** com direção explícita por `checkInAndOutType` ou leitor/porta identificados como ENTRADA/SAÍDA contam como passagem. Não é utilizado `registerTime` ou horário de auto-check-out para fingir uma passagem.
- O endpoint ACS permite consultas por página, mas somente em janelas de no máximo 31 dias. Consultas longas são divididas em períodos. Listas truncadas, erros ou permissões insuficientes não geram horários presumidos.
- Após carregar a lista, até quatro consultas paralelas, cada uma com sua própria sessão OpenAPI, completam as colunas sem bloquear edição ou novos cadastros. Ao mudar de página, os resultados antigos não são aplicados na nova tabela. `—` significa sem evento classificável confirmado, não necessariamente que o visitante nunca passou.
- A busca de passagens depende de permissão na API **Access Control / door events** e da direção correta dos leitores/catracas no HikCentral. A homologação depende das respostas reais do servidor do hotel.
- Nenhuma alteração na lógica de cadastro, foto, NFC, validade de ingressos, grupos, níveis, catracas, configurações nem envio de credenciais.

## Preview.33 — Carregamento e diagnóstico do Visitor Access Record

- A aba **Cadastros efetuados** mostra uma barra de carregamento indeterminada e mensagem explícita enquanto busca cadastros no HikCentral e, depois, progresso `X/Y` da consulta aos eventos de acesso da página. Ao abrir **Ver dados / corrigir** e **Ver foto**, o indicador também aparece e é ocultado após sucesso ou falha.
- **Primeira entrada** e **Última saída** continuam exigindo eventos reais de acesso concedido e **direção comprovada**, conforme `checkInAndOutType` ou nome do leitor/porta. Nenhuma data administrativa ou autenticação sem sentido definido é tratada como saída.
- O backend de `acs/v1/door/events` pesquisa primeiro por `personId`; se o servidor não retornar ocorrências, tenta pesquisa pelo nome, **aceitando somente eventos com o Visitor ID original ou cartão e nome coincidentes**. Não aceita homônimos e não reaproveita uma paginação incompleta.
- O rodapé passa a informar quantidade de consultas, eventos retornados, autenticações concedidas e casos sem direção, além do primeiro erro da API quando houver. Selecionando um visitante remoto e clicando **Consultar catracas**, é possível comparar primeira/última autenticação (como no Visitor Access Record) com a primeira entrada/última saída confirmada, sem alterar dados.
- Ainda é necessária homologação com a versão e permissões do HikCentral do hotel: a interface Web Client pode mostrar primeiro/último registro de autenticação mesmo quando a API não fornece direção; o programa não inventará entrada ou saída nesses casos.
- O login, configurações, foto, câmera, NFC, permissões, grupos e cadastro permanecem inalterados.

## Preview.34 — Cadastros efetuados com menos botões

- Removida a linha de ações **Consultar catracas**, **Reenviar credencial**, **Ver dados / corrigir** e **Ver foto (HikCentral)** da aba **Cadastros efetuados**.
- O **duplo clique** na linha continua abrindo a janela com dados, foto e edição (quando as provas remotas de nível, identidade e credenciais permitirem).
- Consulta do histórico ACS para preencher as colunas de entrada e saída continua automática; nenhum evento ou permissão foi alterado.
- Preservados os filtros, paginação, carregamento, cadastro de visitantes, câmera, NFC, níveis, horários, integração OpenAPI e o botão de salvar existente no editor.
- Teste da interface Windows verifica ausência dos quatro botões, preservação do duplo clique e controles de lista.

## Preview.35 — Visitantes fora do histórico e renovação segura de check-in ativo

- Quando o operador pesquisa por **nome**, a aba Cadastros efetuados busca também em `POST /artemis/api/visitor/v1/visitor/visitorInfo`, a **Visitor Information** do HikCentral, mesmo que a pessoa não tenha check-in no intervalo selecionado. A consulta central de check-ins e os filtros continuam funcionando.
- Pessoas encontradas apenas na base geral são exibidas com **sem check-in no período** e horários em branco (—); o duplo clique abre os detalhes e a foto, **somente para consulta**. Não são usadas datas artificiais, nível presumido nem IDs de outros hóspedes. Essa busca mostra a primeira página de até 100 pessoas retornadas pela API para um nome; refine o nome caso haja muitos resultados.
- Para check-ins ainda **ativos**, a edição exige identidade e `recordId` confirmados na consulta de check-in. Primeiro busca a reserva original; se ela não tiver associação única, pode validar **um único nível** retornado explicitamente pelo `visitor/single/visitorinfo`, com o mesmo Visitor ID, grupo, ID do nível e portas ainda existentes. Sem essas provas a edição permanece somente leitura.
- Um check-in encerrado (`check-out`, saída no terminal ou saída automática) **não deve ser reaberto pela API de edição de check-in**: a documentação do HikCentral explica que o checkout revoga as permissões. Para uma nova estadia, o operador usa **Visitor Information → Registrar novamente** no Web Client, reutilizando dados e credenciais do mesmo visitante. Automatizar esse novo registro exige validar o fluxo próprio da versão instalada no servidor.
- Nenhum novo cadastro nem reserva é criado automaticamente ao pesquisar ou visualizar. As alterações da Preview.35 não mexem em NFC, câmera, grupos, validade, agenda, parâmetros de integração ou níveis existentes.
- Testes cobrem busca por nome na base geral, proteção contra misturar pessoas, visitante sem datas e atualização de check-in ativo com nível real validado, sem operações remotas de escrita nos testes.

## Preview.36 — Resolver pendência, sem bloquear consulta dos visitantes

- Quando existe `%LOCALAPPDATA%\\ValeCatracaSimples\\pending.json`, o botão do rodapé muda de **Verificar última entrega** para **Resolver pendência**. Clicar em **Cadastrar visitante** enquanto há uma operação interrompida também abre essa janela, em vez de apenas avisar e repetir tentativas silenciosas.
- A tela informa **etapa (`stage`)**, horário da última alteração, Visitor ID, reserva, registro de check-in, validade, nível e número de catracas disponíveis no journal. Não grava nem inventa nome, foto, NFC ou credenciais; o journal pode não ter Visitor ID caso o envio da reserva tenha sido interrompido antes da resposta.
- Quando a reserva retornou IDs e níveis verificáveis, **Conferir check-in e retomar credencial** usa a recuperação já existente: consulta o check-in original antes de continuar o envio de níveis/credenciais. **Nunca repete a criação da reserva**. Mostra o resultado ao operador.
- Quando a resposta original é incerta ou os dados não permitem conciliar, a tela explica a revisão humana necessária em **Visitor Reservation** e **Visitor Check-In/Out**. A opção **Arquivar pendência conferida e liberar** exige digitar `CONFERIDO` e confirmar novamente. Ela **não executa escrita no HikCentral**: apenas move o journal local para `pending_reviewed_*.json`, mantendo auditoria, e libera a estação. É preciso antes verificar que não se duplicará um cadastro remoto.
- A **visualização de visitantes, fotos e dados** continua possível mesmo com pendência. O editor abre em modo somente leitura e bloqueia salvamento até resolver a operação. Cadastros novos e atualizações não podem ignorar a trava de segurança.
- Os testes automatizados cobrem resumo da pendência sem IDs, retomada possível com IDs, recusa de confirmação ausente, arquivamento auditável, proteção contra journal modificado e interface Windows sem gravação durante pendência.
- Preservadas as regras de validade, NFC, câmera, fotografias, grupos, níveis e acesso; nenhuma alteração em servidores ou catracas ocorre ao abrir a janela.

## Preview.37 — Editar visitante ativo mesmo quando OpenAPI omite nível/foto

- O editor valida o **mesmo Visitor ID, o mesmo recordId e o check-in ativo** antes de permitir atualização; nunca cria uma nova reserva. Visitas encerradas seguem usando Registrar novamente no Web Client.
- O nível é recuperado preferencialmente de `accessInfo.accessLevelList` fornecido pela reserva, pelos detalhes do visitante ou pelo próprio registro de check-in; diferenças de IDs ou níveis continuam bloqueadas.
- Quando TODAS essas fontes omitem os níveis, mas existe um mapeamento de grupo homologado (`VALE_VISITOR_LEVELS`), valida em tempo real que o nível Visitor `type=2` e seus pontos continuam presentes e com a quantidade aprovada. Nesse modo o operador recebe uma advertência explícita e **deve autorizar a reaplicação desse nível**, que poderá substituir permissões antigas não retornadas. Sem confirmação, nenhuma gravação ocorre. A interface não apresenta essa operação como preservação comprovada do nível anterior.
- A recuperação de foto tenta referências fornecidas pelos dados do visitante e os tipos pequeno/grande no endpoint original, sem fazer requests diretos a URLs de imagem. Se a OpenAPI recusar a imagem (ex.: código interno 128), o editor **permite selecionar/capturar uma foto substituta**, mas bloqueia salvar sem a nova foto quando houver face anterior possível.
- A pulseira NFC original deverá estar presente nos detalhes, no registro consultado ou ser reinformada antes de atualizar. O campo `qrCodeValidNum` permanece fixo em 0, e a operação de edição mantém a verificação de pontos e a confirmação antes de enviar para o HikCentral.
- As ações reais permanecem sujeitas às permissões da instalação HikCentral. O ambiente de CI testa respostas simuladas e inicialização do EXE, mas **não comprova a aceitação de uma atualização real do Visitor ID 1009** nem resolve o endpoint de fotos quando o servidor devolve erro.
- O ZIP local contendo credenciais/DPAPI nunca é publicado nem incluído neste repositório.

## Preview.38 — Alinhamento de confirmação do nível Visitor

- Quando a OpenAPI omite `accessInfo.accessLevelList`, o editor continua validando **Visitor ID, registro do check-in ativo, nome do grupo, ID do nível Visitor `type=2` e IDs exatos das catracas**. Níveis divergentes retornados pelo servidor bloqueiam a edição.
- A edição com reaplicação do nível `VISITANTE_VALE` exige aceite explícito uma primeira vez. **Somente se o HikCentral aceitar a atualização, a entrega às catracas retornar `confirmed` e uma nova consulta reencontrar a pessoa e o período corretos**, o app registra um comprovante limitado ao **mesmo check-in e plano de acesso** no histórico `visitor_history.dpapi` do Windows.
- Por até **7 dias**, quando a API continuar omitindo o nível, o comprovante protegido pode dispensar **a repetição da autorização de reaplicação idêntica**. Mostra uma informação neutra de que a última aplicação foi confirmada nesta estação. **Isso não constitui prova de associação atual no HikCentral** e não detecta necessariamente alterações por outros computadores. Uma nova visita, outra data, outro Visitor ID/check-in, grupo, nível ou porta, ou o fim do prazo, exigem autorização novamente.
- O comprovante não guarda foto, cartão NFC, chave OpenAPI nem senha. Ele é descartado quando o registro local muda. Se a entrega for `queued`, falhar, ou não houver releitura confiável, o comprovante não é salvo; o aviso reaparece para não sugerir uma confirmação inexistente.
- A consulta da OpenAPI `privilege/group/single/personList` com `type=2` é indicada como não suportada para visitantes em algumas versões; **nenhuma solução trata a lista de grupos locais como prova remota**. A verificação de permissões de outros computadores requer API compatível ou serviço compartilhado de auditoria.
- Nenhuma regra de NFC, foto, validade, agenda, câmeras, endpoints de escrita ou grupos é alterada.

## Preview.39 — Grupo selecionável diretamente na edição

- O editor de um check-in existente agora mostra o **mesmo seletor de grupo da tela Cadastro**, com grupos sincronizados pela OpenAPI. A seleção preenche o nível definido em `Configurações > Níveis`. O mesmo bloqueio de grupo preferencial da estação continua aplicável.
- A mensagem laranja `ACESSO EM RECUPERAÇÃO` e o modal redundante de recuperação de nível foram removidos da tela. Há **uma única confirmação antes da gravação**, com Visitor ID, grupo escolhido e nível a aplicar; trocar de grupo é destacado porque pode aumentar ou diminuir acesso.
- O nível selecionado vem do mapeamento homologado, e o aplicativo revalida **grupo existente no HikCentral, nível Visitor `type=2`, contagem e IDs das portas** imediatamente antes da atualização. O programa não considera o grupo configurado como comprovação do acesso antigo.
- Mesmo trocando de grupo, a edição exige o MESMO Visitor ID e o MESMO recordId de check-in, e verifica que o grupo remoto original não mudou após a abertura da janela. Só permite trocar o plano para outro grupo cuja configuração local seja homologada e esteja disponível no servidor; nunca usa nível de funcionários.
- Sem troca de grupo, o aplicativo mantém a comparação de níveis e catracas existentes com a última leitura. A mudança deliberada de grupo aplica o novo conjunto de portas; os novos dados são persistidos no histórico local sem criar novo visitante nem reserva.
- A foto, o NFC, as regras de validade (`qrCodeValidNum=0`), recuperação de pendências e todos os outros parâmetros permanecem inalterados. A homologação da atualização real de grupo depende da configuração OpenAPI do servidor do hotel.

## Preview.40 — interface ágil e limpeza de cache de transações

- A aba **Cadastros efetuados** é construída somente na primeira abertura, uma vez por execução. Navegar até ela **não consulta o histórico criptografado local nem a OpenAPI**. O operador inicia a consulta usando **Atualizar lista**, **Buscar** ou a paginação. Visitar a aba repetidamente reutiliza a mesma tabela (sem empilhar camadas ou duplicar widgets).
- A sincronização de grupos e níveis necessária ao Cadastro continua automática, em uma thread separada. Consultas pesadas de históricos de acessos ficam restritas à tela de Cadastros e só são iniciadas por solicitação.
- Em **Configurações > Manutenção da estação**, botão **Limpar cache (transações)**. Ele atua apenas sobre `pending.json`, `visitor_edit_inflight.json` e `last_delivery.json`, sem coringas e sem apagar `settings.json`, backups de configuração, `secrets.dpapi`, `visitor_history.dpapi`, fotografias ou qualquer arquivo desconhecido.
- A limpeza exige a senha de Configurações, consulta manual do estado das operações em **Visitor Reservation** e **Visitor Check-In/Out**, confirmação dupla e a palavra `CONFERIDO`. Os arquivos ativos são movidos (bytes preservados) para `transaction_audit/` no perfil Windows do operador. Nada é reenviado, apagado do HikCentral ou desfeito no servidor.
- A operação recusa limpeza se outra tarefa estiver em andamento ou se arquivos mudarem durante a revisão; journals modificados não são liberados silenciosamente. Uma reserva já aceita remotamente pode continuar existente: liberar a trava não autoriza cadastrar novamente o mesmo hóspede.
- Os testes automatizados verificam a lista de arquivos permitidos, a preservação de configuração e histórico, recusa sem confirmação, snapshot stale, symlink e reutilização da tabela de histórico sem consultas automáticas.

## Preview.41 — Cadastros do dia e paginação ajustável

- Em **Cadastros efetuados**, os campos **De** e **Até** começam com a data corrente do Windows convertida para `America/Sao_Paulo` (hoje → hoje). O operador continua podendo escolher outros dias. Como na Preview.40, a tela não consulta o HikCentral até clicar em **Atualizar lista** ou **Buscar**.
- Campo **Por página** com as opções **10, 50, 100, 200, 500** (10 por padrão). O número é aplicado à paginação real do servidor: lotes de 200/500 são compostos por requisições de até 100 registros, evitando limites em versões mais antigas da OpenAPI. O botão de paginação avança em páginas lógicas daquele tamanho.
- **Primeira entrada / Última saída** são lidas exclusivamente do histórico ACS `/artemis/api/acs/v1/door/events`, sem confundir com início e fim da validade. Se a OpenAPI exigir `eventType` e a consulta inicial vier vazia/falhar, tenta modalidades de acesso comuns (face, face+cartão, multifator, QR). Isso não cobre todas as modalidades possíveis; os horários podem ser parciais e são mostrados somente quando identidade, evento concedido e direção são comprovados.
- Para reduzir pesquisas vazias, o programa busca os IDs de portas reais do nível Visitor homologado uma única vez por grupo da página (somente leitura). A ausência de permissão para consultar portas não atribui acessos e não impede consultar eventos por Visitor ID.
- O rodapé diferencia: **sem eventos**, **eventos com pessoa/ID divergente**, **autenticações sem sentido ENTRADA/SAÍDA**, **falha de permissão da OpenAPI** e **busca parcial por modalidade**. O Web Client pode ter dados não retornados pela OpenAPI dessa instalação; nunca inventar horários.
- A listagem não busca previamente centenas de reservas para conferir níveis que nem são exibidos: a verificação de acesso permanece sob demanda no editor. Câmera, NFC, arquivos de configuração, limpeza de transações e políticas de acesso não foram modificados.
- Testes automatizados usam dados simulados e não verificam o servidor HikCentral do hotel. Os valores reais de Primeira Entrada / Última Saída devem ser homologados comparando a tela com **Visitor Access Record** do Web Client.

## Preview.42 — Primeira entrada por direção comprovada do leitor

- A consulta ACS já retornava algumas saídas, porém parte dos eventos de entrada vinha com `checkInAndOutType=0` ou sem o nome/direção do leitor. Esses eventos apareciam no diagnóstico como **sem direção** e a coluna **Primeira entrada** continuava vazia.
- Para cada nível Visitor homologado da página, consulta **uma vez por porta** `POST /artemis/api/resource/v1/reader/search` (somente leitura) e relaciona `readerIndexCode`/nome do leitor retornado pelo HikCentral. Leitores com nomes explícitos **ENTRADA/ENT, SAÍDA/SAI, ENTRANCE/ENTRY, EXIT** podem classificar eventos antes sem sentido; também reconhece nomes como **CATRACA ENTRADA 1** e **LEITOR SAÍDA 1**, não apenas nomes que começam pela direção.
- Se a API retornar `checkInAndOutType=1/2`, essa direção documentada continua prioritária. Quando o nome contradiz o mapeamento, o sistema não inventa passagem. Nomes genéricos (**Cardreader 01**) e ambíguos (**ENTRADA E SAIDA**) permanecem sem direção até a nomenclatura ser corrigida no HikCentral.
- Ao selecionar uma linha na lista, o rodapé mostra quantidade de eventos concedidos, eventos sem sentido e até 5 **IDs de leitores sem mapeamento** (sem expor número de cartão, CPF, foto, credenciais ou dados biométricos). Isso ajuda a ajustar leitores pela identificação real do servidor.
- A consulta é apenas de leitura, não altera níveis, visitantes, câmera, NFC ou catracas. Testes simulados verificam os dois sentidos, conflitos e rejeição de nome ambíguo. A validação final depende de eventos reais da instalação HikCentral.


## Preview.43 — check-in Visitor: resposta 131 e reconciliação sem duplicatas

- No POST `/artemis/api/visitor/v1/registerment`, a estrutura `VisitorInfo` inclui `watchListInfo` neutro (`enableRegister=1, ID=0, Type=1`) conforme exemplo da referência OpenAPI consultada. A reserva anterior e os IDs originais são preservados.
- Em respostas internas **código 131**, o programa não considera HTTP 200 um sucesso e não envia um segundo check-in. Consulta o histórico de check-in por `visitorId` e pelo período exato: prossegue apenas com **um** registro remoto confirmado.
- Quando a API retorna sucesso sem o ID opcional do check-in, também faz consulta de leitura para obter confirmação. Sem confirmação única, mantém `pending.json` e impede novo cadastro automático.
- A mensagem de confirmação diferencia ID recebido ou localizado por consulta. Testes simulados abrangem payload, erro 131 sem check-in, 131 reconciliado e sucesso sem ID.
- **Limite:** a causa precisa do código 131 depende dos registros protegidos da instalação; esta versão ainda requer teste operacional de check-in, associação de nível e entrega facial/NFC no HikCentral real. Não foram criados visitantes reais durante os testes automatizados.


## Preview.44 — respeitar a data/hora escolhida de Entrada

**Motivo:** registros com início válido no minuto do caixa (ex.: 16:44). A Preview.29 introduziu atualização do formulário pelo relógio do Windows na abertura, ao cadastrar e após concluir uma venda. Era possível transmitir a hora do atendimento como início da validade quando o operador não digitava outra hora.

- Na abertura, a sugestão de Entrada passa a ser **o horário de abertura do ingresso configurado** (por padrão 09:00), e não a hora atual. Antes do encerramento do ingresso usa a data de hoje; a partir do encerramento usa o dia seguinte.
- As caixas **Entrada** e **Saída** são campos editáveis `DD/MM/AAAA HH:MM`, não um controle de calendário. Depois de inicializar, o aplicativo não substitui seu conteúdo automaticamente pelo relógio ao clicar em CADASTRAR nem depois de um cadastro concluído. Isso permite registrar vários visitantes para o mesmo período selecionado, sem mudar a validade por acidente.
- Alterar Entrada recalcula somente a Saída conforme dias/horário final do tipo de ingresso. Se a hora manual for inválida para o expediente, o texto original permanece no campo e a validação do cadastro impede o envio; não arredonda silenciosamente a entrada para outro dia.
- O botão de confirmação continua mostrando a data e hora completas que serão enviadas. `registration_payload` usa `parse_local(self.start.get())` e `parse_local(self.end.get())`, propagando os valores para `appointStartTime`/`appointEndTime` da reserva e `visitStartTime`/`visitEndTime` do check-in. O timestamp `registered_at` / `registerTime` permanece separado e representa o instante real do cadastramento.
- As agendas e os níveis aprovados no HikCentral **não mudam**. Os testes cobrem 16:44, entrada manual, horário padrão, horário customizado, mudança de data, confirmação do payload e UI Windows. A correção não altera cadastros já existentes: revisar esses registros por edição autorizada, sem recriar reservas.
