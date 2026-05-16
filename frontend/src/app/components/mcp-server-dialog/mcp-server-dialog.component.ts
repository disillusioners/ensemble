import { Component, signal, computed, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import type { McpServer, McpServerCreate, McpServerUpdate } from '../../models';

interface DialogData {
  server?: McpServer;
}

@Component({
  selector: 'app-mcp-server-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule],
  templateUrl: './mcp-server-dialog.html',
  styleUrl: './mcp-server-dialog.scss'
})
export class McpServerDialogComponent implements OnInit {
  protected readonly name = signal('');
  protected readonly description = signal('');
  protected readonly configJson = signal('');
  protected readonly isActive = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly configJsonError = signal<string | null>(null);

  private readonly dialogRef = inject(MatDialogRef<McpServerDialogComponent>);
  protected readonly data = inject<DialogData>(MAT_DIALOG_DATA);

  protected readonly isEditMode = computed(() => !!this.data?.server);

  constructor() {}

  ngOnInit(): void {
    if (this.data?.server) {
      // Edit mode - pre-fill form
      const server = this.data.server;
      this.name.set(server.name);
      this.description.set(server.description || '');
      this.isActive.set(server.is_active);
      if (server.config && Object.keys(server.config).length > 0) {
        this.configJson.set(JSON.stringify(server.config, null, 2));
      }
    }
  }

  protected onNameChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.name.set(target.value);
  }

  protected onDescriptionChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.description.set(target.value);
  }

  protected onConfigJsonChange(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.configJson.set(target.value);
    // Validate JSON
    this.validateConfigJson();
  }

  protected onIsActiveChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.isActive.set(target.checked);
  }

  private validateConfigJson(): boolean {
    const json = this.configJson().trim();
    if (!json) {
      this.configJsonError.set(null);
      return true;
    }

    try {
      JSON.parse(json);
      this.configJsonError.set(null);
      return true;
    } catch {
      this.configJsonError.set('Invalid JSON format');
      return false;
    }
  }

  protected handleClose(): void {
    this.dialogRef.close(null);
  }

  protected handleSubmit(): void {
    // Clear previous error
    this.error.set(null);

    // Validation - Name
    const nameValue = this.name().trim();
    if (!nameValue) {
      this.error.set('Name is required');
      return;
    }
    if (nameValue.length > 128) {
      this.error.set('Name must be 128 characters or less');
      return;
    }

    // Validate JSON config
    if (!this.validateConfigJson()) {
      return;
    }

    // Build result
    let config: Record<string, unknown> | undefined;
    const configJson = this.configJson().trim();
    if (configJson) {
      config = JSON.parse(configJson);
    }

    if (this.isEditMode() && this.data?.server) {
      // Edit mode
      const update: McpServerUpdate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive()
      };
      console.log('Closing dialog with update:', update);
      this.dialogRef.close(update);
    } else {
      // Create mode
      const create: McpServerCreate = {
        name: nameValue,
        description: this.description().trim() || null,
        config,
        is_active: this.isActive()
      };
      console.log('Closing dialog with create:', create);
      this.dialogRef.close(create);
    }
  }

  protected isSubmitDisabled(): boolean {
    return !this.name().trim() || this.configJsonError() !== null;
  }
}
