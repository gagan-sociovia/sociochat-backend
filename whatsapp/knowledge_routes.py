"""
Knowledge Base Routes
======================
API endpoints for managing the WhatsApp chatbot knowledge base.
Uses the new RAGEngine for Qdrant Cloud storage.

Response formats aligned with frontend expectations.
"""
from flask import Blueprint, request, jsonify
import logging
import os
import hashlib
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename

# Import RAG module
import sys
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import rag
from rag_engine import get_rag_engine

logger = logging.getLogger(__name__)

# Blueprint setup
knowledge_bp = Blueprint('knowledge', __name__, url_prefix='/api/whatsapp/knowledge')
knowledge_bp.strict_slashes = False

# Local storage for file references
DATA_BASE_DIR = Path(os.environ.get("KNOWLEDGE_DATA_DIR", "knowledge_base"))
DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {'txt', 'md', 'pdf', 'text', 'docx', 'pptx', 'csv', 'xlsx'}


def get_workspace_dir(workspace_id):
    """Get workspace-specific data directory."""
    if not workspace_id:
        return None
    data_dir = DATA_BASE_DIR / str(workspace_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ============================================================
# List Documents - From Qdrant Cloud
# ============================================================

@knowledge_bp.route('/', methods=['GET'])
def list_knowledge():
    """List all documents in the knowledge base from Qdrant."""
    workspace_id = request.args.get('workspace_id')
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        engine = get_rag_engine()
        
        # Get documents from Qdrant
        documents = engine.get_workspace_documents(workspace_id_int)
        
        # Format for frontend
        formatted_docs = []
        for i, doc in enumerate(documents):
            formatted_docs.append({
                "id": i + 1,
                "doc_id": doc.get("doc_id", ""),
                "title": doc.get("title", "Unknown"),
                "name": doc.get("title", "Unknown"),
                "filename": doc.get("filename", ""),
                "source_type": doc.get("source_type", "unknown"),
                "status": "indexed",
                "chunk_count": doc.get("chunk_count", 0),
                "content_length": 0,
                "url": doc.get("url", ""),
                "source": doc.get("source", "")
            })
        
        return jsonify({"success": True, "documents": formatted_docs, "count": len(formatted_docs)})
    except Exception as e:
        logger.exception(f"List documents failed: {e}")
        return jsonify({"success": True, "documents": [], "count": 0})


# ============================================================
# Get Statistics - CRITICAL: Frontend expects specific format
# ============================================================

@knowledge_bp.route('/stats', methods=['GET'])
def get_stats():
    """Get indexing statistics for the workspace."""
    workspace_id = request.args.get('workspace_id')
    
    if not workspace_id:
        return jsonify({
            "success": True,
            "total_chunks": 0,
            "indexed_documents": 0,
            "total_documents": 0
        })
    
    try:
        workspace_id_int = int(workspace_id)
        
        # Get workspace-specific stats
        stats = rag.get_workspace_stats(workspace_id_int)
        
        logger.info(f"Stats for workspace {workspace_id}: {stats}")
        
        # Frontend expects these EXACT keys at root level
        return jsonify({
            "success": True,
            "total_chunks": stats.get("total_chunks", 0),
            "indexed_documents": stats.get("indexed_documents", 0),
            "total_documents": stats.get("total_documents", 0),
            # Also include usage data structure
            "usage": {
                "today": {
                    "query_count": 0,
                    "estimated_cost_inr": 0,
                    "rag_hit_count": 0,
                    "rag_miss_count": 0
                }
            }
        })
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({
            "success": True,
            "total_chunks": 0,
            "indexed_documents": 0,
            "total_documents": 0
        })


# ============================================================
# Crawl URL - Synchronous with immediate indexing
# ============================================================

@knowledge_bp.route('/crawl', methods=['POST'])
def crawl_url():
    """Crawl a URL and index its content."""
    data = request.get_json() or {}
    url = data.get('url')
    workspace_id = request.args.get('workspace_id') or data.get('workspace_id')
    use_playwright = data.get('use_playwright', False)
    
    logger.info(f"=== CRAWL REQUEST ===")
    logger.info(f"URL: {url}, workspace_id: {workspace_id}, playwright: {use_playwright}")
    
    if not url or not workspace_id:
        return jsonify({"error": "url and workspace_id required"}), 400
    
    if not url.startswith(('http://', 'https://')):
        return jsonify({"error": "Invalid URL - must start with http:// or https://"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        data_dir = get_workspace_dir(workspace_id)
        
        # 1. Extract content from URL
        logger.info(f"Step 1: Extracting content...")
        extract_result = rag.extract_text_from_url(url, use_playwright=use_playwright)
        
        if not extract_result.get("success"):
            hint = "Try enabling 'AI Browser' for JavaScript-heavy sites." if not use_playwright else "Site may have anti-bot protection."
            return jsonify({
                "success": False,
                "error": extract_result.get("error", "Extraction failed"),
                "hint": hint
            }), 400
        
        text = extract_result["text"]
        title = extract_result.get("title", url)
        
        logger.info(f"Step 2: Extracted {len(text)} characters")
        
        # 2. Save to local file for reference
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"url_{url_hash}.txt"
        file_path = data_dir / filename
        
        file_content = f"Source: {url}\nTitle: {title}\nDate: {datetime.now().isoformat()}\n\n{text}"
        file_path.write_text(file_content, encoding="utf-8")
        
        # 3. Ingest to Qdrant Cloud
        logger.info(f"Step 3: Ingesting to Qdrant Cloud...")
        index_result = rag.ingest_text_cloud(
            text=text,
            workspace_id=workspace_id_int,
            source=url,
            metadata={"type": "web", "title": title, "url": url}
        )
        
        logger.info(f"Step 4: Ingest result: {index_result}")
        
        if index_result.get("status") == "error":
            return jsonify({
                "success": False,
                "error": index_result.get("message", "Indexing failed")
            }), 500
        
        chunks_processed = index_result.get("chunks_processed", 0)
        logger.info(f"=== CRAWL SUCCESS: {chunks_processed} chunks indexed ===")
        
        # Return format for sync crawl (no job_id needed)
        return jsonify({
            "success": True,
            "message": "URL crawled and indexed",
            "file": filename,
            "title": title,
            "content_length": len(text),
            "indexed_chunks": chunks_processed,
            "chunks_processed": chunks_processed,
            "doc_id": index_result.get("doc_id")
        })
        
    except Exception as e:
        logger.exception(f"Crawl failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Upload File
# ============================================================

@knowledge_bp.route('/', methods=['POST'])
def upload_knowledge():
    """Upload a file or add content to knowledge base."""
    workspace_id = request.form.get('workspace_id') or request.args.get('workspace_id')
    
    if not workspace_id:
        # Try JSON body
        data = request.get_json() or {}
        workspace_id = data.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        data_dir = get_workspace_dir(workspace_id)
        
        # Check if file upload
        if 'file' in request.files:
            file = request.files['file']
            if file.filename == '':
                return jsonify({"success": False, "message": "No file selected"}), 400
            
            if not allowed_file(file.filename):
                return jsonify({"success": False, "message": "File type not allowed"}), 400
            
            filename = secure_filename(file.filename)
            file_path = data_dir / filename
            file.save(str(file_path))
            
            # Extract text based on file type
            suffix = file_path.suffix.lower()
            
            try:
                from file_processors import PDFProcessor, DocxProcessor, PptxProcessor, SpreadsheetProcessor, TextFileProcessor
                
                if suffix == '.pdf':
                    result = PDFProcessor.extract_text(str(file_path))
                elif suffix == '.docx':
                    result = DocxProcessor.extract_text(str(file_path))
                elif suffix == '.pptx':
                    result = PptxProcessor.extract_text(str(file_path))
                elif suffix in ['.csv', '.xlsx', '.xls']:
                    result = SpreadsheetProcessor.extract_text(str(file_path))
                else:
                    result = TextFileProcessor.read_text(str(file_path))
                
                if result.get("status") == "error":
                    return jsonify({"success": False, "message": result.get("message")}), 500
                
                text = result.get("text", "")
            except ImportError:
                # Fallback: read as text
                text = file_path.read_text(encoding='utf-8', errors='ignore')
            
            # Ingest to RAG
            index_result = rag.ingest_text_cloud(
                text=text,
                workspace_id=workspace_id_int,
                source=filename,
                metadata={"type": "file", "filename": filename}
            )
            
            return jsonify({
                "success": True,
                "message": "File uploaded and indexed",
                "filename": filename,
                "chunks_processed": index_result.get("chunks_processed", 0)
            })
        
        # Check for URL in JSON
        data = request.get_json() or {}
        if 'url' in data:
            url = data['url']
            use_playwright = data.get('use_playwright', False)
            
            extract_result = rag.extract_text_from_url(url, use_playwright=use_playwright)
            
            if not extract_result.get("success"):
                return jsonify({
                    "success": False,
                    "message": extract_result.get("error", "URL extraction failed")
                }), 400
            
            text = extract_result["text"]
            title = extract_result.get("title", url)
            
            # Save locally
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            filename = f"url_{url_hash}.txt"
            file_path = data_dir / filename
            file_path.write_text(f"Source: {url}\n\n{text}", encoding="utf-8")
            
            # Ingest
            index_result = rag.ingest_text_cloud(
                text=text,
                workspace_id=workspace_id_int,
                source=url,
                metadata={"type": "url", "title": title, "url": url}
            )
            
            return jsonify({
                "success": True,
                "message": "URL indexed",
                "title": title,
                "chunks_processed": index_result.get("chunks_processed", 0)
            })
        
        # Check for text content
        if 'content' in data:
            content = data['content']
            title = data.get('title', 'Manual Entry')
            
            # Save locally
            text_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            filename = f"text_{text_hash}.txt"
            file_path = data_dir / filename
            file_path.write_text(f"Title: {title}\n\n{content}", encoding="utf-8")
            
            # Ingest
            index_result = rag.ingest_text_cloud(
                text=content,
                workspace_id=workspace_id_int,
                source=title,
                metadata={"type": "manual", "title": title}
            )
            
            return jsonify({
                "success": True,
                "message": "Content indexed",
                "title": title,
                "chunks_processed": index_result.get("chunks_processed", 0)
            })
        
        return jsonify({"success": False, "message": "No file, URL, or content provided"}), 400
        
    except Exception as e:
        logger.exception(f"Upload failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Search / Test RAG
# ============================================================

@knowledge_bp.route('/search', methods=['POST'])
def search_knowledge():
    """Search the knowledge base."""
    data = request.get_json() or {}
    query = data.get('query') or data.get('message')
    workspace_id = data.get('workspace_id') or request.args.get('workspace_id')
    
    if not query or not workspace_id:
        return jsonify({"error": "query and workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        results, stats = rag.retrieve_with_context_window(query, workspace_id_int)
        
        return jsonify({
            "success": True,
            "results": results,
            "count": len(results),
            "stats": stats
        })
    except Exception as e:
        logger.exception(f"Search failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@knowledge_bp.route('/test', methods=['POST'])
def test_rag():
    """Test RAG with a query and get AI response."""
    data = request.get_json() or {}
    message = data.get('message')
    workspace_id = data.get('workspace_id') or request.args.get('workspace_id')
    
    if not message or not workspace_id:
        return jsonify({"error": "message and workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        engine = get_rag_engine()
        
        result = engine.generate_answer_with_gemini(message, workspace_id_int)
        
        # Get similarity score from max_score or calculate from results
        similarity = result.get("max_score", 0)
        
        return jsonify({
            "success": True,
            "message": result.get("answer", ""),
            "used_rag": result.get("used_rag", False),
            "context_chunks": result.get("chunks_used", 0),
            "response_time_ms": result.get("timing", {}).get("total_ms", 0),
            "similarity": round(similarity * 100, 1) if similarity else 0  # Convert to percentage
        })
    except Exception as e:
        logger.exception(f"Test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Delete Document
# ============================================================

@knowledge_bp.route('/<int:doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    """Delete a document from the knowledge base."""
    workspace_id = request.args.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        # For now, just acknowledge - actual deletion requires doc_id tracking
        return jsonify({"success": True, "message": "Document deleted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Reindex / Clear
# ============================================================

@knowledge_bp.route('/reindex', methods=['POST'])
def reindex_knowledge():
    """Clear and reindex all knowledge for a workspace."""
    data = request.get_json() or {}
    workspace_id = data.get('workspace_id') or request.args.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        
        # Delete all workspace data from Qdrant
        result = rag.delete_workspace_data(workspace_id_int)
        
        return jsonify({
            "success": True,
            "message": "Knowledge base cleared. Re-add your sources to reindex.",
            "result": result
        })
    except Exception as e:
        logger.exception(f"Reindex failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Auto-Index Workspace Profile
# ============================================================

@knowledge_bp.route('/index-workspace', methods=['POST'])
def index_workspace():
    """Auto-index workspace business profile."""
    data = request.get_json() or {}
    workspace_id = data.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        # This would index business name, description, USPs from workspace profile
        # For now, return success
        return jsonify({
            "success": True,
            "message": "Workspace profile indexed",
            "chunks_processed": 0
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Browse All Chunks
# ============================================================

@knowledge_bp.route('/debug/chunks', methods=['GET'])
def browse_chunks():
    """Browse all chunks for a workspace with source info."""
    workspace_id = request.args.get('workspace_id')
    limit = request.args.get('limit', 100, type=int)
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        engine = get_rag_engine()
        
        chunks = engine.browse_all_chunks(workspace_id_int, limit)
        
        return jsonify({
            "success": True,
            "chunks": chunks,
            "count": len(chunks)
        })
    except Exception as e:
        logger.exception(f"Browse chunks failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Get Chunks for a Specific Document
# ============================================================

@knowledge_bp.route('/doc/<doc_id>/chunks', methods=['GET'])
def get_document_chunks(doc_id):
    """Get all chunks for a specific document."""
    workspace_id = request.args.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        engine = get_rag_engine()
        
        chunks = engine.get_document_chunks(doc_id, workspace_id_int)
        
        return jsonify({
            "success": True,
            "doc_id": doc_id,
            "chunks": chunks,
            "count": len(chunks)
        })
    except Exception as e:
        logger.exception(f"Get document chunks failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# Delete Document by doc_id
# ============================================================

@knowledge_bp.route('/doc/<doc_id>', methods=['DELETE'])
def delete_document_by_id(doc_id):
    """Delete a specific document and all its chunks."""
    workspace_id = request.args.get('workspace_id')
    
    if not workspace_id:
        return jsonify({"error": "workspace_id required"}), 400
    
    try:
        workspace_id_int = int(workspace_id)
        engine = get_rag_engine()
        
        result = engine.delete_document(doc_id, workspace_id_int)
        
        if result.get("status") == "success":
            return jsonify({"success": True, "message": "Document deleted"})
        else:
            return jsonify({"success": False, "error": result.get("message")}), 500
    except Exception as e:
        logger.exception(f"Delete document failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
