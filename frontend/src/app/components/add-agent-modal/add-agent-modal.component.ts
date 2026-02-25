import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { Inject } from '@angular/core';
import type { AgentCreate } from '../../models';

interface ColorOption {
  value: string;
  label: string;
  hex: string;
}

@Component({
  selector: 'app-add-agent-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule],
  templateUrl: './add-agent-modal.html',
  styleUrl: './add-agent-modal.scss'
})
export class AddAgentModalComponent {
  protected readonly id = signal('');
  protected readonly name = signal('');
  protected readonly description = signal('');
  protected readonly icon = signal('🤖');
  protected readonly color = signal('accent-cyan');
  protected readonly isLoading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly iconOptions: string[] = ['🤖', '🚀', '💡', '⚡', '🔧', '📝', '🎯', '🔍', '📊', '🎨', '🛠️', '🌟'];
  
  protected readonly colorOptions: ColorOption[] = [
    { value: 'accent-amber', label: 'Amber', hex: '#f59e0b' },
    { value: 'accent-cyan', label: 'Cyan', hex: '#10a7f7' },
    { value: 'accent-violet', label: 'Violet', hex: '#8b5cf6' },
    { value: 'accent-emerald', label: 'Emerald', hex: '#10b981' },
    { value: 'accent-rose', label: 'Rose', hex: '#f43f5e' },
    { value: 'accent-blue', label: 'Blue', hex: '#3b82f6' },
  ];

  constructor(
    private dialogRef: MatDialogRef<AddAgentModalComponent>,
    @Inject(MAT_DIALOG_DATA) public data: {}
  ) {}

  protected resetForm(): void {
    this.id.set('');
    this.name.set('');
    this.description.set('');
    this.icon.set('🤖');
    this.color.set('accent-cyan');
    this.error.set(null);
  }

  protected handleClose(): void {
    this.resetForm();
    this.dialogRef.close();
  }

  protected onIdChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.id.set(target.value);
  }

  protected onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  protected onDescriptionChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.description.set(target.value);
  }

  protected onIconClick(iconOption: string): void {
    this.icon.set(iconOption);
  }

  protected onColorClick(colorOption: ColorOption): void {
    this.color.set(colorOption.value);
  }

  protected async handleSubmit(): Promise<void> {
    const idValue = this.id();
    const nameValue = this.name();
    
    if (!idValue.trim() || !nameValue.trim()) {
      this.error.set('ID and Name are required');
      return;
    }

    // Validate ID format
    if (!/^[a-z0-9_-]+$/.test(idValue)) {
      this.error.set('ID must be lowercase letters, numbers, hyphens, or underscores');
      return;
    }

    this.isLoading.set(true);
    this.error.set(null);

    try {
      const agent: AgentCreate = {
        id: idValue.trim().toLowerCase(),
        name: nameValue.trim(),
        description: this.description().trim(),
        icon: this.icon(),
        color: this.color(),
      };
      
      this.dialogRef.close(agent);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : 'Failed to create agent');
    } finally {
      this.isLoading.set(false);
    }
  }

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || !this.id().trim() || !this.name().trim();
  }
}
