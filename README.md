# Vale - Cadastro Facial e NFC (Windows)

Versão atual: `1.0.0-preview.21` (prévia técnica; ainda não homologada em catraca física).

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
