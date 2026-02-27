import { Component, signal, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { Inject } from '@angular/core';
import type { SourceCreate, SourceType, Agent } from '../../models';
import { ApiService } from '../../services/api.service';

type ConfigTab = 'simple' | 'json';

interface SourceTypeOption {
  value: SourceType;
  label: string;
  icon: string;
  description: string;
}

// Option for select fields
interface SelectOption {
  value: string;
  label: string;
}

// Simple form field definitions per source type
interface SimpleField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'number' | 'checkbox' | 'select';
  placeholder: string;
  hint?: string;
  required?: boolean;
  section: 'credentials' | 'config';
  defaultValue?: string | number | boolean;
  options?: SelectOption[]; // For select type fields
}

interface SourceTypeConfig {
  fields: SimpleField[];
}

@Component({
  selector: 'app-add-source-modal',
  standalone: true,
  imports: [CommonModule, FormsModule, MatDialogModule],
  templateUrl: './add-source-modal.html',
  styleUrl: './add-source-modal.scss'
})
export class AddSourceModalComponent implements OnInit {
  protected readonly sourceId = signal('');
  protected readonly sourceType = signal<SourceType>('telegram');
  protected readonly name = signal('');
  protected readonly configJson = signal('');
  protected readonly credentialsJson = signal('');
  protected readonly enabled = signal(true);
  protected readonly isLoading = signal(false);
  protected readonly error = signal<string | null>(null);
  
  // Tab state for config section
  protected readonly configTab = signal<ConfigTab>('simple');
  
  // Simple form field values (key -> value)
  protected readonly simpleFieldValues = signal<Record<string, string | number | boolean>>({});
  
  // Agent list for select dropdown
  protected readonly agents = signal<Agent[]>([]);

  // Base source type configurations (without dynamic options)
  private readonly baseSourceTypeConfigs: Record<SourceType, Omit<SourceTypeConfig, 'fields'> & { fields: SimpleField[] }> = {
    telegram: {
      fields: [
        { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11', hint: 'Get from @BotFather on Telegram', required: true, section: 'credentials' },
        { key: 'secret_token', label: 'Secret Token', type: 'text', placeholder: 'my-secret-token', hint: 'Optional: For webhook verification', section: 'config' },
        { key: 'default_agent', label: 'Default Agent', type: 'select', placeholder: 'Select an agent...', hint: 'Agent to handle incoming messages', section: 'config', options: [] },
        { key: 'polling_enabled', label: 'Enable Polling', type: 'checkbox', placeholder: '', hint: 'Use polling instead of webhooks', section: 'config', defaultValue: true },
        { key: 'polling_timeout', label: 'Polling Timeout (sec)', type: 'number', placeholder: '30', hint: 'Long polling timeout', section: 'config', defaultValue: 30 },
      ]
    },
    webhook: {
      fields: [
        { key: 'webhook_url', label: 'Webhook URL Path', type: 'text', placeholder: '/webhook/my-endpoint', hint: 'URL path for receiving requests', required: true, section: 'config' },
        { key: 'secret_key', label: 'Secret Key', type: 'password', placeholder: 'your-secret-key', hint: 'Optional: For request verification', section: 'credentials' },
      ]
    },
    whatsapp: {
      fields: [
        { key: 'phone_number_id', label: 'Phone Number ID', type: 'text', placeholder: '123456789', hint: 'From WhatsApp Business API', required: true, section: 'credentials' },
        { key: 'access_token', label: 'Access Token', type: 'password', placeholder: 'your-access-token', hint: 'Permanent access token', required: true, section: 'credentials' },
        { key: 'verify_token', label: 'Verify Token', type: 'text', placeholder: 'my-verify-token', hint: 'For webhook verification', section: 'config' },
      ]
    },
    discord: {
      fields: [
        { key: 'bot_token', label: 'Bot Token', type: 'password', placeholder: 'your-discord-bot-token', hint: 'From Discord Developer Portal', required: true, section: 'credentials' },
        { key: 'application_id', label: 'Application ID', type: 'text', placeholder: '123456789', hint: 'From Discord Developer Portal', required: true, section: 'credentials' },
        { key: 'guild_id', label: 'Server ID (Guild)', type: 'text', placeholder: '987654321', hint: 'Optional: Restrict to specific server', section: 'config' },
      ]
    }
  };
  
  // Get source type configs with dynamic agent options populated
  private get sourceTypeConfigs(): Record<SourceType, SourceTypeConfig> {
    const agentOptions: SelectOption[] = this.agents().map(agent => ({
      value: agent.id,
      label: agent.name
    }));
    
    const configs: Record<SourceType, SourceTypeConfig> = {} as Record<SourceType, SourceTypeConfig>;
    for (const [type, config] of Object.entries(this.baseSourceTypeConfigs)) {
      configs[type as SourceType] = {
        fields: config.fields.map(field => {
          if (field.key === 'default_agent') {
            return { ...field, options: agentOptions };
          }
          return field;
        })
      };
    }
    return configs;
  }
  
  // Get fields for current source type
  protected readonly currentFields = computed(() => {
    return this.sourceTypeConfigs[this.sourceType()]?.fields ?? [];
  });
  
  // Get credential fields for current source type
  protected readonly credentialFields = computed(() => {
    return this.currentFields().filter(f => f.section === 'credentials');
  });
  
  // Get config fields for current source type
  protected readonly configFields = computed(() => {
    return this.currentFields().filter(f => f.section === 'config');
  });

  protected readonly sourceTypeOptions: SourceTypeOption[] = [
    { value: 'telegram', label: 'Telegram', icon: 'telegram', description: 'Receive messages from Telegram bots' },
    { value: 'webhook', label: 'Webhook', icon: 'webhook', description: 'Receive HTTP POST requests' },
    { value: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp', description: 'Connect via WhatsApp Business API' },
    { value: 'discord', label: 'Discord', icon: 'discord', description: 'Receive messages from Discord bots' }
  ];

  constructor(
    private dialogRef: MatDialogRef<AddSourceModalComponent>,
    @Inject(MAT_DIALOG_DATA) public data: {},
    private api: ApiService
  ) {}

  ngOnInit(): void {
    this.loadAgents();
  }
  
  private loadAgents(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        // Don't show error to user - just continue without agent options
      }
    });
  }

  protected resetForm(): void {
    this.sourceId.set('');
    this.sourceType.set('telegram');
    this.name.set('');
    this.configJson.set('');
    this.credentialsJson.set('');
    this.enabled.set(true);
    this.error.set(null);
    this.configTab.set('simple');
    this.simpleFieldValues.set({});
  }
  
  // Initialize simple fields with default values when source type changes
  protected onSourceTypeChange(type: SourceType): void {
    this.sourceType.set(type);
    // Reset simple field values and apply defaults
    const defaults: Record<string, string | number | boolean> = {};
    const fields = this.sourceTypeConfigs[type]?.fields ?? [];
    for (const field of fields) {
      if (field.defaultValue !== undefined) {
        defaults[field.key] = field.defaultValue;
      }
    }
    this.simpleFieldValues.set(defaults);
    // Clear JSON when switching types
    this.configJson.set('');
    this.credentialsJson.set('');
  }
  
  // Handle simple field value changes
  protected onSimpleFieldChange(key: string, event: Event): void {
    const target = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
    const field = this.currentFields().find(f => f.key === key);
    
    let value: string | number | boolean;
    if (field?.type === 'checkbox') {
      value = (target as HTMLInputElement).checked;
    } else if (field?.type === 'number') {
      value = target.value ? parseInt(target.value, 10) : '';
    } else {
      value = target.value;
    }
    
    this.simpleFieldValues.update(values => ({
      ...values,
      [key]: value
    }));
  }
  
  // Switch between tabs
  protected onConfigTabChange(tab: ConfigTab): void {
    if (tab === 'json' && this.configTab() === 'simple') {
      // Converting from simple to JSON - populate JSON fields
      this.syncSimpleToJson();
    }
    this.configTab.set(tab);
  }
  
  // Sync simple field values to JSON format
  private syncSimpleToJson(): void {
    const values = this.simpleFieldValues();
    const fields = this.currentFields();
    
    const config: Record<string, unknown> = {};
    const credentials: Record<string, unknown> = {};
    
    for (const field of fields) {
      const value = values[field.key];
      if (value !== undefined && value !== '' && value !== field.defaultValue) {
        if (field.section === 'config') {
          config[field.key] = value;
        } else {
          credentials[field.key] = value;
        }
      }
    }
    
    this.configJson.set(Object.keys(config).length > 0 ? JSON.stringify(config, null, 2) : '');
    this.credentialsJson.set(Object.keys(credentials).length > 0 ? JSON.stringify(credentials, null, 2) : '');
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

    let config: Record<string, unknown> = {};
    let credentials: Record<string, unknown> = {};

    if (this.configTab() === 'simple') {
      // Use simple field values
      const values = this.simpleFieldValues();
      const fields = this.currentFields();
      
      // Validate required fields
      for (const field of fields) {
        if (field.required) {
          const value = values[field.key];
          if (value === undefined || value === '' || (typeof value === 'string' && !value.trim())) {
            this.error.set(`${field.label} is required`);
            return;
          }
        }
      }
      
      // Build config and credentials from simple fields
      for (const field of fields) {
        const value = values[field.key];
        if (value !== undefined && value !== '') {
          if (field.section === 'config') {
            config[field.key] = value;
          } else {
            credentials[field.key] = value;
          }
        }
      }
    } else {
      // Use JSON input
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

  protected isSubmitDisabled(): boolean {
    return this.isLoading() || !this.sourceId().trim() || !this.name().trim();
  }
}
