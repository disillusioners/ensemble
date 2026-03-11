import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import type { Source } from '../../models';

interface SourceTypeInfo {
  type: string;
  label: string;
  icon: string;
  color: string;
}

@Component({
  selector: 'app-source-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './source-card.html',
  styleUrl: './source-card.scss'
})
export class SourceCardComponent {
  @Input({ required: true }) source!: Source;
  @Output() start = new EventEmitter<string>();
  @Output() stop = new EventEmitter<string>();
  @Output() delete = new EventEmitter<string>();
  @Output() toggleEnabled = new EventEmitter<Source>();
  @Output() edit = new EventEmitter<Source>();

  protected readonly sourceTypes: Record<string, SourceTypeInfo> = {
    telegram: { type: 'telegram', label: 'Telegram', icon: 'telegram', color: '#229ED9' },
    webhook: { type: 'webhook', label: 'Webhook', icon: 'webhook', color: '#10b981' },
    whatsapp: { type: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp', color: '#25D366' },
    discord: { type: 'discord', label: 'Discord', icon: 'discord', color: '#5865F2' }
  };

  protected get sourceTypeInfo(): SourceTypeInfo {
    return this.sourceTypes[this.source.source_type] || {
      type: this.source.source_type,
      label: this.source.source_type,
      icon: 'source',
      color: '#64748b'
    };
  }

  protected get statusColor(): string {
    switch (this.source.status) {
      case 'running': return '#10b981';
      case 'error': return '#f43f5e';
      case 'starting': return '#f59e0b';
      default: return '#64748b';
    }
  }

  protected get statusLabel(): string {
    switch (this.source.status) {
      case 'running': return 'Running';
      case 'error': return 'Error';
      case 'starting': return 'Starting';
      default: return 'Stopped';
    }
  }

  protected onStart(): void {
    this.start.emit(this.source.source_id);
  }

  protected onStop(): void {
    this.stop.emit(this.source.source_id);
  }

  protected onDelete(): void {
    this.delete.emit(this.source.source_id);
  }

  protected onToggleEnabled(): void {
    this.toggleEnabled.emit(this.source);
  }

  protected onEdit(): void {
    this.edit.emit(this.source);
  }

  protected formatDate(dateStr: string): string {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  }
}
