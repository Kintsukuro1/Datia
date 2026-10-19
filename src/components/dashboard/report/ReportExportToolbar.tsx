import React, { useState } from 'react';
import { QueryResult } from '../../../types';
import {
  FileText,
  Copy,
  Check,
  Download,
  FileSpreadsheet,
  RefreshCw,
  ChevronDown,
  Sliders,
  X,
} from 'lucide-react';
import { reportService } from '../../../features/dashboard/services/report_service';
import { useModalA11y } from '../../../hooks/useModalA11y';
import { getApiErrorMessage } from '../../../shared/api/api_client';

interface ReportExportToolbarProps {
  result: QueryResult;
  copiedReport: boolean;
  onCopyReport: () => void;
  onExportError: (msg: string | null) => void;
}

export const ReportExportToolbar: React.FC<ReportExportToolbarProps> = ({
  result,
  copiedReport,
  onCopyReport,
  onExportError,
}) => {
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [isExportingExcel, setIsExportingExcel] = useState(false);
  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);
  const [isCustomizeOpen, setIsCustomizeOpen] = useState(false);

  // Dialog semantics, Escape, focus containment and focus restore.
  const customizeRef = useModalA11y<HTMLDivElement>(isCustomizeOpen, () =>
    setIsCustomizeOpen(false)
  );

  // Customization fields
  const [customTitle, setCustomTitle] = useState(result.question || '');
  const [customNotes, setCustomNotes] = useState('');
  const [includeRawData, setIncludeRawData] = useState(true);

  const handleExportPdf = async (customized = false) => {
    setIsExportingPdf(true);
    onExportError(null);
    setIsExportMenuOpen(false);
    if (customized) setIsCustomizeOpen(false);
    try {
      const chartBase64 = await reportService.captureChartAsBase64();
      await reportService.exportExecutiveReportPdf(result, {
        chartBase64,
        custom_title: customized ? customTitle.trim() : undefined,
        custom_notes: customized ? customNotes.trim() : undefined,
        include_raw_data: customized ? includeRawData : true,
      });
    } catch (err: any) {
      onExportError(getApiErrorMessage(err, 'Error al generar el informe en PDF.'));
    } finally {
      setIsExportingPdf(false);
    }
  };

  const handleExportExcel = async (customized = false) => {
    setIsExportingExcel(true);
    onExportError(null);
    setIsExportMenuOpen(false);
    if (customized) setIsCustomizeOpen(false);
    try {
      await reportService.exportExecutiveReportExcel(result, {
        custom_title: customized ? customTitle.trim() : undefined,
        custom_notes: customized ? customNotes.trim() : undefined,
        include_raw_data: customized ? includeRawData : true,
      });
    } catch (err: any) {
      onExportError(getApiErrorMessage(err, 'Error al generar el archivo Excel.'));
    } finally {
      setIsExportingExcel(false);
    }
  };

  return (
    <>
      <div className="flex items-center space-x-3">
        {/* Copy Report Button */}
        <button
          type="button"
          onClick={onCopyReport}
          aria-label="Copiar informe ejecutivo"
          className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-app-card hover:bg-app-hover text-app-muted hover:text-app-text border border-app-border text-xs font-semibold transition-colors shadow-xs"
        >
          {copiedReport ? <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400" /> : <Copy className="w-4 h-4" />}
          <span>{copiedReport ? '¡Copiado!' : 'Copiar'}</span>
        </button>

        {/* Export Dropdown Menu */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setIsExportMenuOpen((prev) => !prev)}
            disabled={isExportingPdf || isExportingExcel}
            aria-label="Menú exportar informe"
            aria-expanded={isExportMenuOpen}
            className="flex items-center space-x-2 px-4 py-2 rounded-xl bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold transition-colors shadow-sm disabled:opacity-50"
          >
            {isExportingPdf || isExportingExcel ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            <span>Exportar</span>
            <ChevronDown className="w-3.5 h-3.5" />
          </button>

          {isExportMenuOpen && (
            <div className="absolute right-0 mt-2 w-52 rounded-xl bg-app-surface border border-app-border shadow-2xl p-1.5 z-30 space-y-1 animate-fadeIn">
              <button
                type="button"
                onClick={() => handleExportPdf(false)}
                disabled={isExportingPdf}
                className="w-full flex items-center space-x-2 px-3 py-2 text-xs text-app-text hover:bg-app-hover rounded-lg transition-colors text-left"
              >
                <FileText className="w-4 h-4 text-rose-400 shrink-0" />
                <span>Descargar PDF</span>
              </button>
              <button
                type="button"
                onClick={() => handleExportExcel(false)}
                disabled={isExportingExcel}
                className="w-full flex items-center space-x-2 px-3 py-2 text-xs text-app-text hover:bg-app-hover rounded-lg transition-colors text-left"
              >
                <FileSpreadsheet className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>Descargar Excel</span>
              </button>
              <div className="border-t border-app-border my-1"></div>
              <button
                type="button"
                onClick={() => {
                  setIsExportMenuOpen(false);
                  setIsCustomizeOpen(true);
                }}
                className="w-full flex items-center space-x-2 px-3 py-2 text-xs text-brand-700 dark:text-brand-300 hover:bg-brand-500/10 rounded-lg transition-colors text-left"
              >
                <Sliders className="w-4 h-4 text-brand-500 shrink-0" />
                <span>Personalizar Reporte...</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Report Customizer Modal */}
      {isCustomizeOpen && (
        <div ref={customizeRef} role="dialog" aria-modal="true" aria-label="Personalizar informe" tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-xs animate-fadeIn">
          <div className="w-full max-w-lg rounded-2xl bg-app-surface border border-app-border shadow-2xl p-5 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-app-border">
              <div className="flex items-center space-x-2 text-app-text font-semibold text-sm">
                <Sliders className="w-4 h-4 text-brand-500" />
                <span>Personalizar Informe Ejecutivo</span>
              </div>
              <button
                type="button"
                onClick={() => setIsCustomizeOpen(false)}
                className="p-1 rounded-lg text-app-muted hover:text-app-text hover:bg-app-hover transition-colors cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-app-text font-medium mb-1">Título del Reporte</label>
                <input
                  type="text"
                  value={customTitle}
                  onChange={(e) => setCustomTitle(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-app-bg border border-app-border text-app-text placeholder:text-app-muted focus:outline-hidden focus:border-brand-500"
                  placeholder="Ej: Análisis Estratégico de Ventas Q3"
                />
              </div>

              <div>
                <label className="block text-app-text font-medium mb-1">Notas Ejecutivas / Observaciones</label>
                <textarea
                  rows={3}
                  value={customNotes}
                  onChange={(e) => setCustomNotes(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl bg-app-bg border border-app-border text-app-text placeholder:text-app-muted focus:outline-hidden focus:border-brand-500 resize-none"
                  placeholder="Agregue contexto para la junta directiva o instrucciones accionables..."
                />
              </div>

              <div className="flex items-center space-x-2 pt-1">
                <input
                  type="checkbox"
                  id="includeRawDataCheckbox"
                  checked={includeRawData}
                  onChange={(e) => setIncludeRawData(e.target.checked)}
                  className="w-4 h-4 rounded-sm border-app-border text-brand-600 focus:ring-brand-500"
                />
                <label htmlFor="includeRawDataCheckbox" className="text-app-text select-none cursor-pointer">
                  Incluir tabla detallada de datos crudos (Hoja Excel / Anexo PDF)
                </label>
              </div>
            </div>

            <div className="flex items-center justify-end space-x-2 pt-3 border-t border-app-border">
              <button
                type="button"
                onClick={() => setIsCustomizeOpen(false)}
                className="px-3 py-1.5 rounded-xl text-app-muted hover:text-app-text hover:bg-app-hover transition-colors text-xs font-medium cursor-pointer"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={() => handleExportExcel(true)}
                disabled={isExportingExcel}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-emerald-600/15 hover:bg-emerald-600/25 text-emerald-700 dark:text-emerald-300 border border-emerald-500/40 text-xs font-medium transition-colors disabled:opacity-50"
              >
                <FileSpreadsheet className="w-3.5 h-3.5" />
                <span>Exportar Excel</span>
              </button>
              <button
                type="button"
                onClick={() => handleExportPdf(true)}
                disabled={isExportingPdf}
                className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium transition-colors shadow-sm disabled:opacity-50"
              >
                <FileText className="w-3.5 h-3.5" />
                <span>Exportar PDF</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
