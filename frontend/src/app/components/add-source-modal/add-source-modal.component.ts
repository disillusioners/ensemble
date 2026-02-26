import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { Inject } from '@angular/core';
import type { SourceCreate, SourceType } from '../../models';

interface SourceTypeOption {
  value: SourceType;
  label: string;
  icon: string;
  description: string;
}

@Component({
  selector: 'app-add-source-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule],
  templateUrl: './add-source-modal.html',
  styleUrl: './add-source-modal.scss'
})
export class AddSourceModalComponent {
  protected readonly sourceId = signal('');
  protected readonly sourceType = signal<SourceType>('telegram');
  protected readonly name = signal('');
  protected readonly configJson = signal('');
  protected readonly credentialsJson = signal('');
  protected readonly enabled = signal(true);
  protected readonly isLoading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly sourceTypeOptions: SourceTypeOption[] = [
    { value: 'telegram', label: 'Telegram', icon: 'telegram', description: 'Receive messages from Telegram bots' },
    { value: 'webhook', label: 'Webhook', icon: 'webhook', description: 'Receive HTTP POST requests' },
    { value: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp', description: 'Connect via WhatsApp Business API' },
    { value: 'discord', label: 'Discord', icon: 'discord', description: 'Receive messages from Discord bots' }
  ];

  constructor(
    private dialogRef: MatDialogRef<AddSourceModalComponent>,
    @Inject(MAT_DIALOG_DATA) public data: {}
  ) {}

  protected resetForm(): void {
    this.sourceId.set('');
    this.sourceType.set('telegram');
    this.name.set('');
    this.configJson.set('');
    this.credentialsJson.set('');
    this.enabled.set(true);
    this.error.set(null);
  }

  protected handleClose(): void {
    this.resetForm();
    this.dialogRef.close();
  }

  protected onSourceIdChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.sourceId.set(target.value);
  }

  protected onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  protected onSourceTypeChange(type: SourceType): void {
    this.sourceType.set(type);
  }

  protected onConfigJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.configJson.set(target.value);
  }

  protected onCredentialsJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.credentialsJson.set(target.value);
  }

  protected onEnabledChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.enabled.set(target.checked);
  }

  protected async handleSubmit(): Promise<void> {
    const idValue = this.sourceId();
    const nameValue = this.name();
    
    // Validation - Source ID
    if (!idValue.trim()) {
      this.error.set('Source ID is required');
      return;
    }
    if (idValue.length > 64) {
      this.error.set('Source ID must be 64 characters or less');
      return;
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(idValue)) {
      this.error.set('Source ID must contain only letters, numbers, underscores, and hyphens');
      return;
    }

    // Validation - Name
    if (!nameValue.trim()) {
      this.error.set('Name is required');
      return;
    }
    if (nameValue.length > 128) {
      this.error.set('Name must be 128 characters or less');
      return;
    }

    // Validate JSON if provided
    let config: Record<string, unknown> = {};
    let credentials: Record<string, unknown> = {};

    if (this.configJson().trim()) {
      try {
        config = JSON.parse(this.configJson());
      } catch {
        this.error.set('Invalid JSON in Config field');
        return;
      }
    }

    if (this.credentialsJson().trim()) {
      try {
        credentials = JSON.parse(this.credentialsJson());
      } catch {
        this.error.set('Invalid JSON in Credentials field');
        return;
      }
    }

    this.isLoading.set(true);
    this.error.set(null);

    try {
      const source: SourceCreate = {
        source_id: idValue.trim().toLowerCase(),
        source_type: this.sourceType(),
        name: nameValue.trim(),
        config: Object.keys(config).length > 0 ? config : undefined,
        credentials: Object.keys(credentials).length > 0 ? credentials : undefined,
        enabled: this.enabled()
      };
      
      this.dialogRef.close(source);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : 'Failed to create source');
    } finally {
      this.isLoading.set(false);
    }
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || !this.sourceId().trim() || !this.name().trim();
  }
}
