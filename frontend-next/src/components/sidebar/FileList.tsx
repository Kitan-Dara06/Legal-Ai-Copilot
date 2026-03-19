import { useState, useMemo } from "react";
import { type FileItem } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Search, FileText, ChevronLeft, ChevronRight, X, Trash2 } from "lucide-react";
import { Button } from "../ui/Button";

interface FileListProps {
  files: FileItem[];
  selectedIds: number[];
  onToggleSelection: (id: number) => void;
  onDeleteFile: (id: number) => void;
  isLoading: boolean;
}

const ITEMS_PER_PAGE = 10;

export function FileList({ files, selectedIds, onToggleSelection, onDeleteFile, isLoading }: FileListProps) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  const filteredFiles = useMemo(() => {
    if (!search.trim()) return files;
    const lower = search.toLowerCase();
    return files.filter(f => f.filename.toLowerCase().includes(lower));
  }, [files, search]);

  const totalPages = Math.ceil(filteredFiles.length / ITEMS_PER_PAGE);
  const paginatedFiles = filteredFiles.slice(page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "READY":
        return <Badge variant="success">Ready</Badge>;
      case "PROCESSING":
        return <Badge variant="warning">Processing</Badge>;
      case "FAILED":
        return <Badge variant="error" className="cursor-help" title="Failed to embed document">Failed</Badge>;
      default:
        return <Badge variant="info">Pending</Badge>;
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4 py-8">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 bg-slate-800/50 rounded-xl animate-pulse" />
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-navy-900 rounded-2xl border border-slate-800 overflow-hidden shadow-lg">
      <div className="p-4 border-b border-slate-800">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            type="text"
            placeholder="Search documents..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
            className="w-full bg-slate-800/50 border border-slate-700/50 rounded-lg pl-9 pr-8 py-2 text-sm text-slate-200 focus:outline-none focus:ring-1 focus:ring-accent-blue focus:border-accent-blue transition-colors"
          />
          {search && (
            <button
              onClick={() => { setSearch(""); setPage(0); }}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {filteredFiles.length === 0 ? (
          <div className="p-8 text-center text-slate-500 text-sm">
            {search ? "No documents found matching criteria" : "Your document library is empty"}
          </div>
        ) : (
          <ul className="divide-y divide-slate-800/50">
            {paginatedFiles.map(f => (
              <li 
                key={f.file_id}
                className={`group flex items-center justify-between p-4 hover:bg-slate-800/30 transition-colors cursor-pointer ${selectedIds.includes(f.file_id) ? "bg-accent-blue/10 hover:bg-accent-blue/20" : ""}`}
                onClick={() => onToggleSelection(f.file_id)}
              >
                <div className="flex items-center gap-3 overflow-hidden">
                  <div className={`shrink-0 w-5 h-5 rounded border flex items-center justify-center transition-colors ${selectedIds.includes(f.file_id) ? "border-accent-blue bg-accent-blue text-white" : "border-slate-600 bg-slate-900/50"}`}>
                    {selectedIds.includes(f.file_id) && <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" /></svg>}
                  </div>
                  <FileText className="w-5 h-5 text-slate-400 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-200 truncate pr-4">{f.filename}</p>
                    <p className="text-xs text-slate-500">{new Date(f.upload_date).toLocaleDateString()}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  {getStatusBadge(f.status)}
                  <button
                    onClick={(e) => { e.stopPropagation(); onDeleteFile(f.file_id); }}
                    className="p-1.5 text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all rounded hover:bg-slate-800/50"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {totalPages > 1 && (
        <div className="p-3 border-t border-slate-800 flex items-center justify-between bg-slate-900/50">
          <p className="text-xs text-slate-500">
            Showing {page * ITEMS_PER_PAGE + 1}-{Math.min((page + 1) * ITEMS_PER_PAGE, filteredFiles.length)} of {filteredFiles.length}
          </p>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded text-slate-400"
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded text-slate-400"
              disabled={page >= totalPages - 1}
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
