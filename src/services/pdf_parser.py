import pypdf
import io

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Reads a PDF byte stream and returns extracted text."""
    try:
        pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in pdf_reader.pages]
        return "\n\n".join(pages).strip()
    except Exception as e:
        print(f"Error parsing PDF: {str(e)}")
        return ""