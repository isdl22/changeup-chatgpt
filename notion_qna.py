import dotenv
dotenv.load_dotenv()

from pathlib import Path
from langchain.text_splitter import CharacterTextSplitter
import faiss
from langchain.vectorstores import FAISS
from langchain.embeddings import OpenAIEmbeddings
import pickle


# Here we load in the data in the format that Notion exports it in.
ps = list(Path("Notion_DB/").glob("**/*.md"))

data = []
sources = []
for p in ps:
    with open(p) as f:
        data.append(f.read())
    sources.append(p)

# 읽은 데이터가 있는지 확인합니다.
if not data:
    print("데이터가 로드되지 않았습니다.")
else:
    print(f"로드된 문서 수: {len(data)}")

text_splitter = CharacterTextSplitter(chunk_size=1000, separator="\n")
docs = []
metadatas = []
for i, d in enumerate(data):
    splits = text_splitter.split_text(d)
    docs.extend(splits)
    metadatas.extend([{"source": sources[i]}] * len(splits))

# 분할된 문서와 메타데이터가 올바른지 검증합니다.
if not docs or not metadatas:
    print("문서가 분할되지 않았거나 메타데이터가 생성되지 않았습니다.")
else:
    print(f"분할된 문서 수: {len(docs)}")
    print(f"첫 번째 문서의 내용: {docs[0][:200]}")
    print(f"첫 번째 문서의 메타데이터: {metadatas[0]}")

# Here we create a vector store from the documents and save it to disk.
store = FAISS.from_texts(docs, OpenAIEmbeddings(), metadatas=metadatas)
faiss.write_index(store.index, "docs.index")
store.index = None
with open("faiss_store.pkl", "wb") as f:
    pickle.dump(store, f)