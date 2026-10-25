# Vale - Cadastro Facial e NFC (Windows)

Versão atual: `1.0.0-preview.7` (prévia técnica; ainda não homologada em catraca física).

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
