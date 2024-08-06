import torch
from transformers import LlamaTokenizer, LlamaForCausalLM, pipeline
import sqlite3
from tqdm import tqdm

class LlamaQAGenerator:
    def __init__(self, input_db="code_data.db", output_db="qa_pairs_llama.db"):
        self.tokenizer = LlamaTokenizer.from_pretrained("meta-llama/Meta-Llama-3.1-8B-Instruct")  # Replace if using a different tokenizer
        self.model = LlamaForCausalLM.from_pretrained("meta-llama/Meta-Llama-3.1-8B-Instruct", torch_dtype=torch.float16, device_map='auto')
        self.input_db = input_db
        self.output_db = output_db
        self.setup_database()
        # Ensure you have a GPU available
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    def setup_database(self):
        with sqlite3.connect(self.output_db) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS qa_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT,
                    source_title TEXT,
                    content TEXT,
                    question TEXT,
                    answer TEXT,
                    qa_type TEXT
                )
            ''')

    def process_data(self):
        pipe = pipeline(
            "text-generation",
            model=self.model,
            tokenizer=self.tokenizer,
            max_length=1024,
            temperature=0.7,
            top_p=0.95,
            repetition_penalty=1.15
        )
        with sqlite3.connect(self.input_db) as conn:
            cursor = conn.execute("SELECT * FROM code_data")
            rows = cursor.fetchall()
            for row in tqdm(rows, desc="Generating Q&A pairs"):
                url, title, content, code_block, language, tokens, source_type = row[1:]

                qa_pairs = []

                # Generate code-focused Q&A (if code is present)
                if code_block:
                    qa_pairs.extend(self._generate_code_qa(url, title, content, code_block, language, pipe))

                # Generate concept-focused Q&A (always generate at least one)
                qa_pairs.extend(self._generate_concept_qa(url, title, content, source_type, pipe))

                self._store_qa_pairs(url, title, content, qa_pairs)

    def _generate_code_qa(self, url, title, content, code_block, language, pipe):
        prompt = f"""Generate a detailed question that requires a full, working code solution based on the following code snippet (in {language}) and context from {url} (titled "{title}"):

        Code:
        ```{language}
        {code_block}
        ```

        Context:
        {content[:500]}
        
        Question:"""

        output_sequences = pipe(prompt)
        question = output_sequences[0]['generated_text'][len(prompt):].strip()
        
        prompt += f"\n{question}\nAnswer:"
        
        output_sequences = pipe(prompt)
        answer = output_sequences[0]['generated_text'][len(prompt):].strip()

        return [{"question": question, "answer": answer, "type": "code"}]
        


    def _generate_concept_qa(self, url, title, content, source_type, pipe):
        prompt = f"""Generate a question and answer pair based on the following content from {url} (titled "{title}"):

        Content:
        {content[:500]}

        The question should focus on a key concept or principle from the content.
        The answer should explain the concept, its importance, and practical applications.

        Consider the source type of the content ({source_type}) when crafting the question.
        
        Question:"""

        output_sequences = pipe(prompt)
        question = output_sequences[0]['generated_text'][len(prompt):].strip()

        prompt += f"\n{question}\nAnswer:"

        output_sequences = pipe(prompt)
        answer = output_sequences[0]['generated_text'][len(prompt):].strip()

        return [{"question": question, "answer": answer, "type": "concept"}]

    def _store_qa_pairs(self, url, title, content, qa_pairs):
        with sqlite3.connect(self.output_db) as conn:
            for pair in qa_pairs:
                conn.execute(
                    "INSERT INTO qa_pairs (source_url, source_title, content, question, answer, qa_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (url, title, content, pair["question"], pair["answer"], pair["type"])
                )