# app.py
# Usado para criar o servidor web e receber requisições
import openpyxl  # Importa para gerar planilhas Excel
import pandas as pd  # Importa para manipulação de dados (útil para gráficos)
import matplotlib.pyplot as plt  # Importa para gerar gráficos
from flask import Flask, request
# Usado para construir as respostas do Twilio
from twilio.twiml.messaging_response import MessagingResponse
# Usado para enviar mensagens (opcional, MessagingResponse já serve para responder ao webhook)
from twilio.rest import Client
import google.generativeai as genai  # Biblioteca para interagir com a Gemini API
import os  # Usado para acessar variáveis de ambiente (do nosso arquivo .env)
from dotenv import load_dotenv  # Usado para carregar as variáveis do .env
import json  # Importa a biblioteca json no topo, para uso global
import unicodedata  # Importa para normalização de texto (remover acentos)
import sqlite3  # Importa a biblioteca para trabalhar com SQLite
# Importa para lidar com datas e horas
from datetime import datetime, timedelta

# --- IMPORTANTE: Configura o backend do Matplotlib para não usar GUI ---
# Esta linha DEVE vir ANTES de qualquer importação de matplotlib.pyplot
import matplotlib
matplotlib.use('Agg')  # Define o backend 'Agg' para gerar imagens sem GUI
# -------------------------------------------------------------------


# -------------------------------------------------------------------
# 1. Carregar Variáveis de Ambiente
# Isso pega as chaves do seu arquivo .env para que o código possa usá-las.
load_dotenv()

# -------------------------------------------------------------------
# 2. Configurações Globais
app = Flask(__name__)  # Inicializa nosso aplicativo Flask

# Pega as credenciais do Twilio do arquivo .env
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
# O número do Sandbox da Twilio, formatado para WhatsApp
TWILIO_WHATSAPP_NUMBER = "whatsapp:" + \
    os.getenv("TWILIO_WHATSAPP_SANDBOX_NUMBER")

# Pega a chave da Gemini API do arquivo .env e configura a API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
# Define qual modelo Gemini vamos usar. Usando gemini-1.5-flash para eficiência.
model = genai.GenerativeModel('gemini-1.5-flash')

# Cliente Twilio (usado para enviar mensagens proativamente, mas MessagingResponse é para responder ao webhook)
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# -------------------------------------------------------------------
# Configuração do Banco de Dados SQLite
DATABASE = 'despesas.db'  # Nome do arquivo do banco de dados
GRAPHS_DIR = 'graphs'  # Diretório para salvar os gráficos
EXCEL_DIR = 'excel_reports'  # Novo diretório para salvar as planilhas Excel


def init_db():
    """Inicializa o banco de dados, criando a tabela de despesas se ela não existir."""
    conn = sqlite3.connect(
        DATABASE)  # Conecta ou cria o arquivo do banco de dados
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            value REAL NOT NULL,
            category TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()  # Salva as mudanças
    conn.close()  # Fecha a conexão
    print("Banco de dados inicializado com sucesso.")

    # Cria o diretório de gráficos se não existir
    if not os.path.exists(GRAPHS_DIR):
        os.makedirs(GRAPHS_DIR)
        print(f"Diretório '{GRAPHS_DIR}' criado.")

    # Cria o diretório de relatórios Excel se não existir
    if not os.path.exists(EXCEL_DIR):
        os.makedirs(EXCEL_DIR)
        print(f"Diretório '{EXCEL_DIR}' criado.")


def save_expense(user_id, value, category):
    """Salva uma despesa no banco de dados."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    # Pega a data e hora atual no formato ISO
    timestamp = datetime.now().isoformat()
    cursor.execute('''
        INSERT INTO expenses (user_id, value, category, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (user_id, value, category, timestamp))
    conn.commit()
    conn.close()
    print(
        f"Despesa salva: Usuário={user_id}, Valor={value}, Categoria={category}, Data={timestamp}")
    return cursor.lastrowid  # Retorna o ID da despesa inserida


def get_expense_by_id(expense_id, user_id):
    """Busca uma despesa pelo ID e user_id."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, value, category, timestamp FROM expenses WHERE id = ? AND user_id = ?', (expense_id, user_id))
    expense = cursor.fetchone()
    conn.close()
    return expense


def delete_expense(expense_id, user_id):
    """Deleta uma despesa pelo ID e user_id."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM expenses WHERE id = ? AND user_id = ?', (expense_id, user_id))
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    print(
        f"Despesa deletada: ID={expense_id}, Usuário={user_id}. Linhas afetadas: {rows_affected}")
    return rows_affected > 0  # Retorna True se alguma linha foi deletada


def update_expense(expense_id, user_id, new_value=None, new_category=None):
    """Atualiza uma despesa existente pelo ID e user_id."""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    update_fields = []
    params = []

    if new_value is not None:
        update_fields.append("value = ?")
        params.append(new_value)
    if new_category is not None:
        update_fields.append("category = ?")
        params.append(new_category)

    if not update_fields:
        conn.close()
        return False  # Nada para atualizar

    query = f"UPDATE expenses SET {', '.join(update_fields)} WHERE id = ? AND user_id = ?"
    params.extend([expense_id, user_id])

    cursor.execute(query, tuple(params))
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    print(
        f"Despesa atualizada: ID={expense_id}, Usuário={user_id}, Valor={new_value}, Categoria={new_category}. Linhas afetadas: {rows_affected}")
    return rows_affected > 0  # Retorna True se alguma linha foi atualizada


def get_expenses(user_id, period_type=None, category=None, limit=None):
    """
    Busca despesas para um usuário, com filtros de período, categoria e limite.
    period_type pode ser: 'hoje', 'este_mes', 'ultimos_7_dias', 'ultimas_x', 'total'.
    """
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    query = "SELECT id, value, category, timestamp FROM expenses WHERE user_id = ?"
    params = [user_id]

    # Filtrar por período
    if period_type == 'hoje':
        today = datetime.now().strftime('%Y-%m-%d')
        query += " AND substr(timestamp, 1, 10) = ?"
        params.append(today)
    elif period_type == 'este mês':
        this_month = datetime.now().strftime('%Y-%m')
        query += " AND substr(timestamp, 1, 7) = ?"
        params.append(this_month)
    elif period_type == 'ultimos 7 dias':
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
        query += " AND timestamp >= ?"
        params.append(seven_days_ago)
    elif period_type == 'ultimas_x' and limit is not None:
        pass
    elif period_type == 'total':
        pass

    # Filtrar por categoria
    if category:
        normalized_category = normalize_text(category)
        query += " AND lower(category) LIKE ?"
        params.append(f"%{normalized_category}%")

    query += " ORDER BY timestamp DESC"

    if period_type == 'ultimas_x' and limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, tuple(params))
    expenses = cursor.fetchall()
    conn.close()
    return expenses


def generate_expense_graph(user_id, period_type=None, category=None):
    """
    Gera um gráfico de barras das despesas por categoria e salva como imagem.
    Retorna o caminho do arquivo da imagem gerada.
    """
    print(
        f"DEBUG: Gerando gráfico para user_id={user_id}, period_type='{period_type}', category='{category}'")
    expenses_data = get_expenses(user_id, period_type, category)

    print(f"DEBUG: Dados de despesas recuperados: {expenses_data}")

    if not expenses_data:
        print("DEBUG: Nenhuma despesa encontrada para gerar gráfico.")
        return None

    df = pd.DataFrame(expenses_data, columns=[
                      'id', 'value', 'category', 'timestamp'])
    print(f"DEBUG: DataFrame criado:\n{df}")

    category_summary = df.groupby(
        'category')['value'].sum().sort_values(ascending=False)
    print(f"DEBUG: Resumo por categoria:\n{category_summary}")

    # Certifica-se que a figura é criada e fechada corretamente
    fig, ax = plt.subplots(figsize=(10, 6))  # Cria uma figura e um eixo

    if not category_summary.empty:
        category_summary.plot(kind='bar', color='skyblue',
                              ax=ax)  # Plota no eixo criado
        ax.set_title(
            f'Gastos por Categoria {f"({period_type})" if period_type else ""}{f" em {category}" if category else ""}')
        ax.set_xlabel('Categoria')
        ax.set_ylabel('Valor (R$)')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gastos_{user_id}_{timestamp_str}.png"
        filepath = os.path.join(GRAPHS_DIR, filename)

        try:
            plt.savefig(filepath)  # Salva o gráfico como imagem
            print(f"DEBUG: Gráfico salvo em: {filepath}")
            return filepath
        except Exception as e:
            print(
                f"ERRO: Falha ao salvar o gráfico em {filepath}. Detalhes: {e}")
            return None
        finally:
            plt.close(fig)  # Fecha a figura para liberar memória, sempre
    else:
        print("DEBUG: Resumo por categoria vazio, não é possível plotar o gráfico.")
        plt.close(fig)  # Fecha a figura mesmo que vazia
        return None


def generate_expense_excel(user_id, period_type=None, category=None):
    """
    Gera uma planilha Excel com as despesas e salva como arquivo .xlsx.
    Retorna o caminho do arquivo da planilha gerada.
    """
    print(
        f"DEBUG: Gerando planilha Excel para user_id={user_id}, period_type='{period_type}', category='{category}'")
    expenses_data = get_expenses(user_id, period_type, category)

    if not expenses_data:
        print("DEBUG: Nenhuma despesa encontrada para gerar planilha Excel.")
        return None

    # Cria um novo Workbook e seleciona a planilha ativa
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Relatório de Despesas"

    # Adiciona cabeçalhos
    headers = ["ID", "Valor", "Categoria", "Data e Hora"]
    ws.append(headers)

    # Adiciona os dados
    for exp in expenses_data:
        # Formata a data para ser mais legível no Excel
        formatted_timestamp = datetime.fromisoformat(
            exp[3]).strftime('%Y-%m-%d %H:%M:%S')
        ws.append([exp[0], exp[1], exp[2], formatted_timestamp])

    # Calcula o total
    total_value = sum(e[1] for e in expenses_data)
    ws.append([])  # Linha em branco
    ws.append(["Total Geral", total_value, "", ""])  # Adiciona o total

    # Ajusta a largura das colunas
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter  # Get the column name
        for cell in col:
            try:  # Necessary to avoid error on empty cells
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2)
        ws.column_dimensions[column].width = adjusted_width

    # Gera um nome de arquivo único
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"despesas_{user_id}_{timestamp_str}.xlsx"
    filepath = os.path.join(EXCEL_DIR, filename)

    try:
        wb.save(filepath)  # Salva a planilha
        print(f"DEBUG: Planilha Excel salva em: {filepath}")
        return filepath
    except Exception as e:
        print(
            f"ERRO: Falha ao salvar a planilha Excel em {filepath}. Detalhes: {e}")
        return None


# -------------------------------------------------------------------
# Função para normalizar texto (remover acentos e converter para minúsculas)
def normalize_text(text):
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('utf-8').lower()

# -------------------------------------------------------------------
# 3. Rota para o Webhook do WhatsApp
# Esta função será chamada pela Twilio toda vez que uma mensagem chegar


@app.route('/whatsapp', methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '')
    incoming_msg_normalized = normalize_text(incoming_msg)

    from_number = request.values.get('From', '')

    print(f"Mensagem recebida de {from_number}: {incoming_msg}")
    print(f"Mensagem normalizada para comparação: {incoming_msg_normalized}")

    resp = MessagingResponse()
    msg = resp.message()

    intent_prompt = f"""
    Você é um assistente financeiro. Sua tarefa é identificar a intenção do usuário e extrair os dados relevantes.
    As intenções possíveis são:
    - "saudacao": para cumprimentos como "olá" ou "oi".
    - "registrar_despesa": para adicionar uma nova despesa.
    - "deletar_despesa": para remover uma despesa existente.
    - "editar_despesa": para modificar uma despesa existente.
    - "consultar_gastos": para ver um resumo ou lista de despesas.
    - "gerar_grafico": para gerar um gráfico de despesas.
    - "gerar_planilha": para gerar uma planilha Excel com despesas.
    - "nao_entendido": se a intenção não for clara.

    Para "registrar_despesa", extraia "valor" (número decimal) e "categoria" (string). Se não houver categoria, use "Outros".
    Para "deletar_despesa", extraia "id" (número inteiro) da despesa a ser deletada.
    Para "editar_despesa", extraia "id" (número inteiro), "novo_valor" (número decimal, opcional) e "nova_categoria" (string, opcional).
    Para "consultar_gastos", "gerar_grafico" e "gerar_planilha", extraia "periodo" (string, ex: "hoje", "este mês", "últimos 7 dias", "total", "últimas X") e "categoria" (string, opcional). Se o período for "últimas X", extraia também "limite" (número inteiro).

    Sua resposta DEVE ser um objeto JSON válido, sem nenhum texto extra antes ou depois, e conter APENAS as chaves "intent" e os parâmetros relevantes.

    Exemplos de entrada e saída esperada (APENAS O JSON):
    Frase: Olá
    Resposta: {{"intent": "saudacao"}}

    Frase: Gastei 50 reais no mercado.
    Resposta: {{"intent": "registrar_despesa", "valor": 50.00, "categoria": "Mercado"}}

    Frase: Farmacia 35
    Resposta: {{"intent": "registrar_despesa", "valor": 35.00, "categoria": "Farmácia"}}

    Frase: 35 farmacia
    Resposta: {{"intent": "registrar_despesa", "valor": 35.00, "categoria": "Farmácia"}}

    Frase: 150 de gasolina
    Resposta: {{"intent": "registrar_despesa", "valor": 150.00, "categoria": "Combustível"}}

    Frase: Conta de luz 80
    Resposta: {{"intent": "registrar_despesa", "valor": 80.00, "categoria": "Contas de Casa"}}

    Frase: Deletar despesa 123
    Resposta: {{"intent": "deletar_despesa", "id": 123}}

    Frase: Excluir o lançamento 45
    Resposta: {{"intent": "deletar_despesa", "id": 45}}

    Frase: Editar despesa 67 para 150 em transporte
    Resposta: {{"intent": "editar_despesa", "id": 67, "novo_valor": 150.00, "nova_categoria": "Transporte"}}

    Frase: Editar 88 categoria aluguel
    Resposta: {{"intent": "editar_despesa", "id": 88, "nova_categoria": "Aluguel"}}

    Frase: Quanto gastei este mês?
    Resposta: {{"intent": "consultar_gastos", "periodo": "este mês"}}

    Frase: Meus gastos de hoje
    Resposta: {{"intent": "consultar_gastos", "periodo": "hoje"}}

    Frase: Quanto gastei em mercado semana passada?
    Resposta: {{"intent": "consultar_gastos", "periodo": "ultimos 7 dias", "categoria": "Mercado"}}

    Frase: Últimas 5 despesas
    Resposta: {{"intent": "consultar_gastos", "periodo": "ultimas_x", "limite": 5}}

    Frase: Total de gastos
    Resposta: {{"intent": "consultar_gastos", "periodo": "total"}}

    Frase: Gerar gráfico de gastos do mês
    Resposta: {{"intent": "gerar_grafico", "periodo": "este mês"}}

    Frase: Gráfico de despesas de hoje
    Resposta: {{"intent": "gerar_grafico", "periodo": "hoje"}}

    Frase: Gráfico de mercado
    Resposta: {{"intent": "gerar_grafico", "categoria": "Mercado"}}

    Frase: Gerar planilha de gastos
    Resposta: {{"intent": "gerar_planilha", "periodo": "total"}}

    Frase: Exportar despesas do mês para Excel
    Resposta: {{"intent": "gerar_planilha", "periodo": "este mês"}}

    Frase: Planilha de gastos de transporte
    Resposta: {{"intent": "gerar_planilha", "categoria": "Transporte"}}

    Frase: {incoming_msg}
    Resposta:
    """

    try:
        gemini_response = model.generate_content(intent_prompt)

        raw_gemini_text = gemini_response.text.strip()
        if raw_gemini_text.startswith('```json'):
            raw_gemini_text = raw_gemini_text[len('```json'):].strip()
        if raw_gemini_text.endswith('```'):
            raw_gemini_text = raw_gemini_text[:-len('```')].strip()

        print(f"Resposta bruta do Gemini (após limpeza): {raw_gemini_text}")

        parsed_data = json.loads(raw_gemini_text)
        intent = parsed_data.get("intent")

        if intent == "saudacao":
            response_text = "Olá! Eu sou seu assistente financeiro. Posso te ajudar a registrar, deletar, editar, consultar seus gastos, gerar gráficos e planilhas. Experimente: 'Gastei 50 no mercado', 'Deletar 123', 'Editar 45 valor 100', 'Quanto gastei este mês?', 'Gerar gráfico de gastos do mês', ou 'Gerar planilha de gastos'."

        elif intent == "registrar_despesa":
            valor = parsed_data.get("valor")
            categoria = parsed_data.get("categoria", "Outros")

            if valor is not None:
                new_id = save_expense(from_number, valor, categoria)
                response_text = f"Ok! Registrei uma despesa de R${valor:.2f} na categoria '{categoria}' (ID: {new_id}). Foi salvo no seu histórico."
            else:
                response_text = "Não consegui identificar o valor ou a categoria da sua despesa. Poderia tentar de novo? Por exemplo: 'Farmacia 75' ou '75 farmácia'."

        elif intent == "deletar_despesa":
            expense_id = parsed_data.get("id")
            if expense_id is not None:
                expense_to_delete = get_expense_by_id(expense_id, from_number)
                if expense_to_delete:
                    if delete_expense(expense_id, from_number):
                        response_text = f"Despesa de R${expense_to_delete[1]:.2f} na categoria '{expense_to_delete[2]}' (ID: {expense_id}) foi deletada com sucesso."
                    else:
                        response_text = f"Não foi possível deletar a despesa com ID {expense_id}. Verifique se o ID está correto e se a despesa pertence a você."
                else:
                    response_text = f"Despesa com ID {expense_id} não encontrada ou não pertence a você."
            else:
                response_text = "Para deletar uma despesa, por favor, informe o ID. Ex: 'Deletar despesa 123'."

        elif intent == "editar_despesa":
            expense_id = parsed_data.get("id")
            new_value = parsed_data.get("novo_valor")
            new_category = parsed_data.get("nova_categoria")

            if expense_id is not None and (new_value is not None or new_category is not None):
                expense_to_edit = get_expense_by_id(expense_id, from_number)
                if expense_to_edit:
                    if update_expense(expense_id, from_number, new_value, new_category):
                        response_text = f"Despesa com ID {expense_id} atualizada com sucesso!"
                    else:
                        response_text = f"Não foi possível atualizar a despesa com ID {expense_id}. Verifique se o ID está correto e se a despesa pertence a você."
                else:
                    response_text = f"Despesa com ID {expense_id} não encontrada ou não pertence a você."
            else:
                response_text = "Para editar uma despesa, por favor, informe o ID e o que deseja alterar. Ex: 'Editar 123 valor 100' ou 'Editar 45 categoria Alimentação'."

        elif intent == "consultar_gastos":
            periodo = parsed_data.get("periodo")
            categoria = parsed_data.get("categoria")
            limite = parsed_data.get("limite")

            expenses = get_expenses(from_number, periodo, categoria, limite)

            if expenses:
                # Soma os valores das despesas
                total_value = sum(e[1] for e in expenses)
                response_lines = [
                    f"Seus gastos {periodo or 'totais'}{f' em {categoria}' if categoria else ''}:"]
                response_lines.append(f"Total: R${total_value:.2f}\n")

                # Lista as despesas individualmente (limitando para não encher a tela)
                for i, exp in enumerate(expenses):
                    if i < 5:  # Mostra os primeiros 5 para um resumo rápido
                        response_lines.append(
                            f"ID {exp[0]}: R${exp[1]:.2f} em {exp[2]} ({datetime.fromisoformat(exp[3]).strftime('%d/%m %H:%M')})")
                    elif i == 5:
                        response_lines.append(f"...")

                if len(expenses) > 5:
                    response_lines.append(
                        f"\nExistem mais {len(expenses) - 5} despesas nesse período/categoria. Para ver mais detalhes, você pode exportar para uma planilha em breve!")

                response_text = "\n".join(response_lines)
            else:
                response_text = f"Não encontrei despesas {periodo or 'para o período'}{f' em {categoria}' if categoria else ''}."

        elif intent == "gerar_grafico":
            periodo = parsed_data.get("periodo")
            categoria = parsed_data.get("categoria")

            graph_filepath = generate_expense_graph(
                from_number, periodo, categoria)

            if graph_filepath:
                response_text = f"Gráfico de gastos gerado com sucesso! Você pode encontrá-lo em: {graph_filepath}\n\n" \
                                "No momento, não consigo enviar a imagem diretamente para o WhatsApp, mas estamos trabalhando nisso!"
            else:
                response_text = f"Não há dados suficientes para gerar um gráfico {f'para o período {periodo}' if periodo else ''}{f' em {categoria}' if categoria else ''}."

        elif intent == "gerar_planilha":
            periodo = parsed_data.get("periodo")
            categoria = parsed_data.get("categoria")

            excel_filepath = generate_expense_excel(
                from_number, periodo, categoria)

            if excel_filepath:
                response_text = f"Planilha Excel de gastos gerada com sucesso! Você pode encontrá-la em: {excel_filepath}\n\n" \
                                "No momento, não consigo enviar o arquivo diretamente para o WhatsApp, mas estamos trabalhando nisso!"
            else:
                response_text = f"Não há dados suficientes para gerar uma planilha Excel {f'para o período {periodo}' if periodo else ''}{f' em {categoria}' if categoria else ''}."

        else:  # intent == "nao_entendido" ou intent não reconhecida
            response_text = "Desculpe, não entendi. Sou um assistente financeiro. Tente registrar uma despesa, deletar, editar, consultar, gerar gráficos ou planilhas. Ex: 'Gastei 50 no mercado', 'Deletar 123', 'Editar 45 valor 100', 'Quanto gastei este mês?', 'Gerar gráfico de gastos do mês', 'Gerar planilha de gastos'."

    except json.JSONDecodeError:
        print(
            f"Erro ao decodificar JSON do Gemini. Texto recebido: {raw_gemini_text}")
        response_text = "Ops! Tive um problema para entender sua mensagem. O Gemini não retornou um formato esperado. Poderia tentar de novo?"
    except Exception as e:
        print(f"Erro geral ao processar com Gemini: {e}")
        response_text = "Desculpe, algo deu errado ao processar sua solicitação. Tente novamente."

    # Envia a resposta de volta para o WhatsApp
    msg.body(response_text)
    return str(resp)  # Retorna a resposta para a Twilio


# -------------------------------------------------------------------
# 5. Executar o Aplicativo Flask
# Isso faz com que o servidor Flask comece a rodar na porta 5000.
if __name__ == '__main__':
    init_db()  # Chama a função para inicializar o banco de dados ao iniciar o app
    app.run(debug=True, port=5000)
