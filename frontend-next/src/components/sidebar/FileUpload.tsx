import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { UploadCloud, FileText, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { uploadFiles } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

interface FileUploadProps {
  onUploadSuccess: () => void;
  orgSlug?: string;
}

interface UploadProgress {
  filename: string;
  progress: number;
  status: "uploading" | "success" | "error";
  errorMsg?: string;
}

export function FileUpload({ onUploadSuccess, orgSlug }: FileUploadProps) {
  const [uploads, setUploads] = useState<UploadProgress[]>([]);

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (!acceptedFiles.length) return;

    const newUploads = acceptedFiles.map(f => ({
      filename: f.name,
      progress: 0,
      status: "uploading" as const
    }));
    
    setUploads(prev => [...prev, ...newUploads]);

    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      
      if (!session) throw new Error("Not authenticated");

      // We upload all at once, tracking overall progress
      const result = await uploadFiles(
        session.access_token,
        acceptedFiles,
        orgSlug,
        (pct) => {
          setUploads(prev => prev.map(u => ({ ...u, progress: pct })));
        }
      );

      // Handle backend response per file (since some might be duplicates/errors)
      setUploads(prev => prev.map(u => {
        const res = result.results.find(r => r.filename === u.filename);
        if (!res) return u;
        
        if (res.status === "error") {
          return { ...u, status: "error", errorMsg: res.message || "Upload failed", progress: 0 };
        } else if (res.status === "duplicate") {
          return { ...u, status: "error", errorMsg: "File already exists", progress: 100 };
        }
        return { ...u, status: "success", progress: 100 };
      }));

      // Only trigger refresh if at least one file was accepted
      if (result.results.some(r => r.status === "accepted")) {
        onUploadSuccess();
      }

    } catch (err: any) {
      setUploads(prev => prev.map(u => 
        u.status === "uploading" 
          ? { ...u, status: "error", errorMsg: err.message || "Network error", progress: 0 }
          : u
      ));
    }
  }, [orgSlug, onUploadSuccess]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxSize: 200 * 1024 * 1024, // 200MB
  });

  return (
    <div className="space-y-4">
      <div 
        {...getRootProps()} 
        className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors
          ${isDragActive ? "border-accent-blue bg-accent-blue/5" : "border-slate-800 hover:border-slate-700 bg-slate-900/50"}
        `}
      >
        <input {...getInputProps()} />
        <UploadCloud className={`w-8 h-8 mx-auto mb-3 ${isDragActive ? "text-accent-blue" : "text-slate-500"}`} />
        <p className="text-sm text-slate-300 font-medium">Add new document</p>
        <p className="text-xs text-slate-500 mt-1">Limit 200MB per file • PDF</p>
      </div>

      {/* Upload queue UI */}
      {uploads.length > 0 && (
        <div className="space-y-2 max-h-40 overflow-y-auto">
          {uploads.slice().reverse().map((u, i) => (
            <div key={`${u.filename}-${i}`} className="bg-slate-900 rounded-lg p-3 border border-slate-800">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2 truncate pr-2">
                  <FileText className="w-4 h-4 text-slate-400 shrink-0" />
                  <span className="text-xs text-slate-300 truncate">{u.filename}</span>
                </div>
                {u.status === "uploading" && <span className="text-xs text-accent-blue font-medium">{u.progress}%</span>}
                {u.status === "success" && <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />}
                {u.status === "error" && <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />}
              </div>
              
              {u.status === "uploading" && (
                <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-accent-blue transition-all duration-300 ease-out"
                    style={{ width: `${u.progress}%` }}
                  />
                </div>
              )}
              {u.status === "error" && (
                <p className="text-xs text-red-400 mt-1">{u.errorMsg}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
