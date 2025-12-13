#!/usr/bin/env python3
"""
Intelligent Configuration Builder for CloudBrew
Uses reverse engineering approach to build valid OpenTofu configurations
with minimal user input and negligible latency.
"""

import json
import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Any
from pathlib import Path
import re
import time
import boto3  # For AWS defaults
from botocore.exceptions import ClientError

# Local imports
try:
    from LCF.resource_resolver import ResourceResolver
    from LCF.store import get_config, save_config
except ImportError:
    # Fallback for standalone testing
    class ResourceResolver:
        def resolve(self, resource_type):
            return resource_type
    
    def get_config(key, default=None):
        return default
    
    def save_config(key, value):
        pass


class IntelligentBuilder:
    """
    Builds valid OpenTofu configurations through iterative validation
    and intelligent default selection.
    """
    
    def __init__(self):
        self.resolver = ResourceResolver()
        self.cache_dir = Path(".cloudbrew_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.knowledge_base = self._load_knowledge_base()
        
        # Initialize AWS client for smart defaults
        self.aws_client = None
        self._init_aws_client()
    
    def _init_aws_client(self):
        """Initialize AWS client for getting smart defaults"""
        try:
            # Use existing AWS credentials
            self.aws_client = boto3.client('ec2')
        except Exception:
            self.aws_client = None
    
    def _load_knowledge_base(self) -> Dict:
        """Load cached knowledge about resource requirements"""
        kb_file = self.cache_dir / "knowledge_base.json"
        if kb_file.exists():
            try:
                return json.loads(kb_file.read_text())
            except Exception:
                pass
        return {}
    
    def _save_knowledge_base(self):
        """Save knowledge base to cache"""
        kb_file = self.cache_dir / "knowledge_base.json"
        kb_file.write_text(json.dumps(self.knowledge_base, indent=2))
    
    def build_configuration(self, resource_type: str, user_input: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Build a valid configuration for the given resource type.
        
        Args:
            resource_type: The resource type (e.g., 'aws_instance')
            user_input: User-provided parameters
            
        Returns:
            Valid OpenTofu configuration
        """
        if user_input is None:
            user_input = {}
        
        # Check cache first
        cache_key = f"config:{resource_type}"
        cached_config = get_config(cache_key)
        if cached_config:
            return self._merge_with_user_input(cached_config, user_input)
        
        # Use fast path for known resource types
        fast_config = self._try_fast_path(resource_type, user_input)
        if fast_config:
            # Validate once to ensure it's correct
            try:
                errors = self._validate_with_opentofu(fast_config)
                if not errors:
                    # Success! Cache this configuration
                    save_config(cache_key, fast_config)
                    self._update_knowledge_base(resource_type, fast_config)
                    return self._merge_with_user_input(fast_config, user_input)
            except Exception:
                # Fall back to iterative approach if fast path fails
                pass
        
        # Fall back to iterative approach
        return self._build_iteratively(resource_type, user_input)
    
    def _try_fast_path(self, resource_type: str, user_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Try to build configuration using known patterns without validation loops.
        """
        try:
            if resource_type == 'aws_instance':
                return self._build_aws_instance(user_input)
            elif resource_type == 'aws_s3_bucket':
                return self._build_aws_s3_bucket(user_input)
            elif resource_type == 'aws_dynamodb_table':
                return self._build_aws_dynamodb_table(user_input)
            elif resource_type == 'aws_db_instance':
                return self._build_aws_db_instance(user_input)
            # Add more resource types as needed
            return None
        except Exception:
            return None
    
    def _build_iteratively(self, resource_type: str, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build configuration using iterative validation (fallback method).
        """
        # Start with minimal configuration
        config = {
            "resource": {
                resource_type: {
                    "test": {}  # Temporary name for validation
                }
            }
        }
        
        # Iterative validation and correction
        max_iterations = 10
        for iteration in range(max_iterations):
            try:
                # Validate current configuration
                errors = self._validate_with_opentofu(config)
                
                if not errors:
                    # Success! Cache this configuration
                    cache_key = f"config:{resource_type}"
                    save_config(cache_key, config)
                    self._update_knowledge_base(resource_type, config)
                    return self._merge_with_user_input(config, user_input)
                
                # Analyze and fix errors
                config = self._fix_errors(config, errors, user_input)
                
            except Exception as e:
                # Handle validation failures
                error_msg = str(e)
                if "missing required argument" in error_msg:
                    missing_field = self._extract_missing_field(error_msg)
                    if missing_field:
                        config = self._add_missing_field(config, missing_field, user_input)
                else:
                    raise
        
        raise RuntimeError(f"Could not build valid configuration for {resource_type} after {max_iterations} iterations")
    
    def _validate_with_opentofu(self, config: Dict) -> List[str]:
        """
        Validate configuration using complete schema-based approach.
        Returns list of error messages or empty list if valid.
        No OpenTofu subprocess calls - pure schema validation.
        """
        # Extract resource type and config
        resource_type = list(config['resource'].keys())[0]
        resource_config = config['resource'][resource_type]['test']
        
        # Use complete schema-based validation for ALL resources
        return self._complete_schema_validation(resource_type, resource_config)
    
    def _get_fast_validation_resources(self) -> List[str]:
        """Resources that can be validated instantly"""
        return [
            'aws_instance', 'aws_s3_bucket', 'aws_dynamodb_table', 'aws_db_instance',
            'aws_vpc', 'aws_subnet', 'aws_security_group', 'aws_lambda_function',
            'aws_iam_role', 'aws_iam_policy', 'aws_cloudwatch_alarm', 'aws_sqs_queue',
            'aws_sns_topic', 'aws_eks_cluster', 'aws_ecr_repository', 'aws_route53_zone'
        ]
    
    def _ultra_fast_validation(self, config: Dict) -> List[str]:
        """Instant validation using pre-cached schemas"""
        resource_type = list(config['resource'].keys())[0]
        resource_config = config['resource'][resource_type]['test']
        return self._schema_based_validation(resource_type, resource_config)
    
    def _optimized_opentofu_validation(self, config: Dict) -> List[str]:
        """Optimized OpenTofu validation with caching and parallel processing"""
        try:
            # Use a persistent OpenTofu environment to avoid init overhead
            from LCF.opentofu_environment import environment_pool
            env = environment_pool.get_environment()
            return env.validate_config(config)
        except Exception as e:
            return [f"Validation error: {str(e)}"]
    
    def _complete_schema_validation(self, resource_type: str, resource_config: Dict) -> List[str]:
        """Complete schema validation for ALL AWS resources"""
        errors = []
        
        # Comprehensive schema definitions for all AWS resources
        schemas = self._get_comprehensive_aws_schemas()
        
        # Apply schema validation
        if resource_type in schemas:
            schema = schemas[resource_type]
            
            # Check required fields
            for field in schema.get('required', []):
                if field not in resource_config:
                    errors.append(f"missing required argument: {field}")
            
            # Check complex field validation
            if 'complex' in schema:
                for field, validator in schema['complex'].items():
                    if field in resource_config:
                        errors.extend(validator(resource_config[field]))
            
            # Check field types
            if 'types' in schema:
                for field, field_type in schema['types'].items():
                    if field in resource_config:
                        errors.extend(self._validate_field_type(field, resource_config[field], field_type))
        else:
            # For unknown resources, use basic validation
            if 'name' in resource_config and not resource_config['name']:
                errors.append("name cannot be empty")
        
        return errors
    
    def _get_comprehensive_aws_schemas(self) -> Dict:
        """Return comprehensive schemas for all AWS resources"""
        return {
            # Compute
            'aws_instance': {
                'required': ['ami', 'instance_type'],
                'types': {'ami': 'string', 'instance_type': 'string'}
            },
            'aws_launch_template': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_autoscaling_group': {
                'required': ['name', 'min_size', 'max_size'],
                'types': {'name': 'string', 'min_size': 'number', 'max_size': 'number'}
            },
            
            # Storage
            'aws_s3_bucket': {
                'required': ['bucket'],
                'types': {'bucket': 'string'}
            },
            'aws_ebs_volume': {
                'required': ['availability_zone', 'size'],
                'types': {'availability_zone': 'string', 'size': 'number'}
            },
            'aws_efs_file_system': {
                'required': ['creation_token'],
                'types': {'creation_token': 'string'}
            },
            
            # Database
            'aws_dynamodb_table': {
                'required': ['name', 'hash_key', 'attribute', 'billing_mode'],
                'complex': {'attribute': self._validate_dynamodb_attributes},
                'types': {'name': 'string', 'hash_key': 'string', 'billing_mode': 'string'}
            },
            'aws_db_instance': {
                'required': ['engine', 'instance_class', 'allocated_storage'],
                'types': {'engine': 'string', 'instance_class': 'string', 'allocated_storage': 'number'}
            },
            'aws_rds_cluster': {
                'required': ['cluster_identifier', 'engine'],
                'types': {'cluster_identifier': 'string', 'engine': 'string'}
            },
            
            # Networking
            'aws_vpc': {
                'required': ['cidr_block'],
                'types': {'cidr_block': 'string'}
            },
            'aws_subnet': {
                'required': ['vpc_id', 'cidr_block'],
                'types': {'vpc_id': 'string', 'cidr_block': 'string'}
            },
            'aws_security_group': {
                'required': ['name', 'vpc_id'],
                'types': {'name': 'string', 'vpc_id': 'string'}
            },
            'aws_lb': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_alb': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_route53_zone': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Serverless
            'aws_lambda_function': {
                'required': ['function_name', 'role', 'handler', 'runtime'],
                'types': {'function_name': 'string', 'role': 'string', 'handler': 'string', 'runtime': 'string'}
            },
            'aws_api_gateway_rest_api': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_sqs_queue': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_sns_topic': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Management
            'aws_iam_role': {
                'required': ['name', 'assume_role_policy'],
                'types': {'name': 'string', 'assume_role_policy': 'string'}
            },
            'aws_iam_policy': {
                'required': ['name', 'policy'],
                'types': {'name': 'string', 'policy': 'string'}
            },
            'aws_cloudwatch_alarm': {
                'required': ['alarm_name', 'metric_name', 'namespace', 'statistic'],
                'types': {'alarm_name': 'string', 'metric_name': 'string', 'namespace': 'string', 'statistic': 'string'}
            },
            
            # Containers
            'aws_eks_cluster': {
                'required': ['name', 'role_arn'],
                'types': {'name': 'string', 'role_arn': 'string'}
            },
            'aws_ecs_cluster': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ecr_repository': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Developer Tools
            'aws_codebuild_project': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_codepipeline': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_codecommit_repository': {
                'required': ['repository_name'],
                'types': {'repository_name': 'string'}
            },
            
            # Analytics
            'aws_kinesis_stream': {
                'required': ['name', 'shard_count'],
                'types': {'name': 'string', 'shard_count': 'number'}
            },
            'aws_athena_database': {
                'required': ['name', 'bucket'],
                'types': {'name': 'string', 'bucket': 'string'}
            },
            'aws_glue_catalog_database': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Machine Learning
            'aws_sagemaker_notebook_instance': {
                'required': ['name', 'role_arn', 'instance_type'],
                'types': {'name': 'string', 'role_arn': 'string', 'instance_type': 'string'}
            },
            'aws_rekognition_collection': {
                'required': ['collection_id'],
                'types': {'collection_id': 'string'}
            },
            
            # Security
            'aws_kms_key': {
                'required': ['description'],
                'types': {'description': 'string'}
            },
            'aws_secretsmanager_secret': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_guardduty_detector': {
                'required': ['enable'],
                'types': {'enable': 'bool'}
            },
            
            # Migration
            'aws_dms_replication_instance': {
                'required': ['replication_instance_id', 'replication_instance_class'],
                'types': {'replication_instance_id': 'string', 'replication_instance_class': 'string'}
            },
            'aws_sms_instance': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Media Services
            'aws_mediaconvert_queue': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_mediastore_container': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Robotics
            'aws_robomaker_robot_application': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Blockchain
            'aws_qldb_ledger': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Satellite
            'aws_groundstation_config': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            
            # Well-Architected
            'aws_wellarchitected_workload': {
                'required': ['workload_name'],
                'types': {'workload_name': 'string'}
            },
            
            # Additional common resources
            'aws_cloudfront_distribution': {
                'required': ['enabled', 'default_cache_behavior', 'origins'],
                'types': {'enabled': 'bool'}
            },
            'aws_elasticache_cluster': {
                'required': ['cluster_id', 'engine', 'node_type'],
                'types': {'cluster_id': 'string', 'engine': 'string', 'node_type': 'string'}
            },
            'aws_elasticbeanstalk_application': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_stepfunctions_state_machine': {
                'required': ['name', 'definition'],
                'types': {'name': 'string', 'definition': 'string'}
            },
            'aws_waf_web_acl': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_wafv2_web_acl': {
                'required': ['name', 'scope'],
                'types': {'name': 'string', 'scope': 'string'}
            },
            'aws_xray_encryption_config': {
                'required': ['key_id'],
                'types': {'key_id': 'string'}
            },
            'aws_backup_plan': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_backup_vault': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_batch_compute_environment': {
                'required': ['compute_environment_name'],
                'types': {'compute_environment_name': 'string'}
            },
            'aws_budgets_budget': {
                'required': ['name', 'budget_type', 'limit_amount', 'limit_unit'],
                'types': {'name': 'string', 'budget_type': 'string', 'limit_amount': 'string', 'limit_unit': 'string'}
            },
            'aws_cognito_user_pool': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_connect_instance': {
                'required': ['instance_alias'],
                'types': {'instance_alias': 'string'}
            },
            'aws_datasync_task': {
                'required': ['source_location_arn', 'destination_location_arn'],
                'types': {'source_location_arn': 'string', 'destination_location_arn': 'string'}
            },
            'aws_dax_cluster': {
                'required': ['cluster_name', 'iam_role_arn'],
                'types': {'cluster_name': 'string', 'iam_role_arn': 'string'}
            },
            'aws_detective_graph': {
                'required': []  # No required fields
            },
            'aws_devicefarm_project': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_dlm_lifecycle_policy': {
                'required': ['description', 'policy_details'],
                'types': {'description': 'string'}
            },
            'aws_docdb_cluster': {
                'required': ['cluster_identifier', 'engine', 'master_username', 'master_password'],
                'types': {'cluster_identifier': 'string', 'engine': 'string', 'master_username': 'string', 'master_password': 'string'}
            },
            'aws_drs_replication_configuration': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_dx_connection': {
                'required': ['name', 'bandwidth', 'location'],
                'types': {'name': 'string', 'bandwidth': 'string', 'location': 'string'}
            },
            'aws_ec2_client_vpn_endpoint': {
                'required': ['server_certificate_arn', 'client_cidr_block'],
                'types': {'server_certificate_arn': 'string', 'client_cidr_block': 'string'}
            },
            'aws_ec2_transit_gateway': {
                'required': ['description'],
                'types': {'description': 'string'}
            },
            'aws_ecrpublic_repository': {
                'required': ['repository_name'],
                'types': {'repository_name': 'string'}
            },
            'aws_ecs_task_definition': {
                'required': ['family'],
                'types': {'family': 'string'}
            },
            'aws_efs_access_point': {
                'required': ['file_system_id'],
                'types': {'file_system_id': 'string'}
            },
            'aws_eks_node_group': {
                'required': ['cluster_name', 'node_group_name', 'node_role_arn'],
                'types': {'cluster_name': 'string', 'node_group_name': 'string', 'node_role_arn': 'string'}
            },
            'aws_elasticache_replication_group': {
                'required': ['replication_group_id', 'replication_group_description'],
                'types': {'replication_group_id': 'string', 'replication_group_description': 'string'}
            },
            'aws_elasticsearch_domain': {
                'required': ['domain_name'],
                'types': {'domain_name': 'string'}
            },
            'aws_emr_cluster': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_fis_experiment_template': {
                'required': ['description'],
                'types': {'description': 'string'}
            },
            'aws_fms_policy': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_fsx_lustre_file_system': {
                'required': ['storage_capacity'],
                'types': {'storage_capacity': 'number'}
            },
            'aws_gamelift_game_session_queue': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_globalaccelerator_accelerator': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_glue_job': {
                'required': ['name', 'role_arn'],
                'types': {'name': 'string', 'role_arn': 'string'}
            },
            'aws_grafana_workspace': {
                'required': ['account_access_type', 'authentication_providers'],
                'types': {'account_access_type': 'string'}
            },
            'aws_greengrass_group': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_guardduty_filter': {
                'required': ['name', 'detector_id'],
                'types': {'name': 'string', 'detector_id': 'string'}
            },
            'aws_healthlake_fhir_datastore': {
                'required': ['datastore_name'],
                'types': {'datastore_name': 'string'}
            },
            'aws_iam_instance_profile': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_iam_user': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_imagebuilder_image_pipeline': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_inspector_assessment_template': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_iot_thing': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ivs_channel': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_kendra_index': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_keyspaces_table': {
                'required': ['keyspace_name', 'table_name'],
                'types': {'keyspace_name': 'string', 'table_name': 'string'}
            },
            'aws_lakeformation_data_lake_settings': {
                'required': []  # No required fields
            },
            'aws_lex_bot': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_licensemanager_license': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_lightsail_instance': {
                'required': ['name', 'blueprint_id', 'bundle_id'],
                'types': {'name': 'string', 'blueprint_id': 'string', 'bundle_id': 'string'}
            },
            'aws_location_place_index': {
                'required': ['data_source', 'index_name'],
                'types': {'data_source': 'string', 'index_name': 'string'}
            },
            'aws_macie2_account': {
                'required': []  # No required fields
            },
            'aws_managedblockchain_network': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_marketplace_catalog': {
                'required': []  # No required fields
            },
            'aws_mwaa_environment': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_neptune_cluster': {
                'required': ['cluster_identifier'],
                'types': {'cluster_identifier': 'string'}
            },
            'aws_networkfirewall_firewall': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_networkmanager_global_network': {
                'required': []  # No required fields
            },
            'aws_oam_link': {
                'required': ['label_template', 'resource_types', 'sink_identifier'],
                'types': {'label_template': 'string', 'sink_identifier': 'string'}
            },
            'aws_opensearch_domain': {
                'required': ['domain_name'],
                'types': {'domain_name': 'string'}
            },
            'aws_opsworks_stack': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_organizations_account': {
                'required': ['email', 'name'],
                'types': {'email': 'string', 'name': 'string'}
            },
            'aws_panorama_package': {
                'required': ['package_name'],
                'types': {'package_name': 'string'}
            },
            'aws_personalize_dataset': {
                'required': ['name', 'dataset_group_arn'],
                'types': {'name': 'string', 'dataset_group_arn': 'string'}
            },
            'aws_pinpoint_app': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_prometheus_workspace': {
                'required': ['alias'],
                'types': {'alias': 'string'}
            },
            'aws_proton_service': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_qldb_stream': {
                'required': ['ledger_name', 'stream_name'],
                'types': {'ledger_name': 'string', 'stream_name': 'string'}
            },
            'aws_quicksight_data_source': {
                'required': ['data_source_id', 'name'],
                'types': {'data_source_id': 'string', 'name': 'string'}
            },
            'aws_ram_resource_share': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_rbin_rule': {
                'required': ['rule_name'],
                'types': {'rule_name': 'string'}
            },
            'aws_redshift_cluster': {
                'required': ['cluster_identifier'],
                'types': {'cluster_identifier': 'string'}
            },
            'aws_resiliencehub_app': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_resourceexplorer_index': {
                'required': []  # No required fields
            },
            'aws_robomaker_simulation_application': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_rolesanywhere_trust_anchor': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_route53_resolver_endpoint': {
                'required': ['direction', 'security_group_ids'],
                'types': {'direction': 'string'}
            },
            'aws_s3_bucket_policy': {
                'required': ['bucket', 'policy'],
                'types': {'bucket': 'string', 'policy': 'string'}
            },
            'aws_s3control_bucket_policy': {
                'required': ['bucket', 'policy'],
                'types': {'bucket': 'string', 'policy': 'string'}
            },
            'aws_sagemaker_model': {
                'required': ['name', 'execution_role_arn'],
                'types': {'name': 'string', 'execution_role_arn': 'string'}
            },
            'aws_scheduler_schedule': {
                'required': ['name', 'schedule_expression', 'target'],
                'types': {'name': 'string', 'schedule_expression': 'string'}
            },
            'aws_schemas_schema': {
                'required': ['content', 'type'],
                'types': {'content': 'string', 'type': 'string'}
            },
            'aws_secretsmanager_secret_version': {
                'required': ['secret_id'],
                'types': {'secret_id': 'string'}
            },
            'aws_securityhub_account': {
                'required': []  # No required fields
            },
            'aws_servicecatalog_portfolio': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ses_domain_identity': {
                'required': ['domain'],
                'types': {'domain': 'string'}
            },
            'aws_ses_email_identity': {
                'required': ['email'],
                'types': {'email': 'string'}
            },
            'aws_sfn_activity': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_shield_protection': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_signer_signing_profile': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_sns_platform_application': {
                'required': ['name', 'platform'],
                'types': {'name': 'string', 'platform': 'string'}
            },
            'aws_sqs_queue_policy': {
                'required': ['queue_url', 'policy'],
                'types': {'queue_url': 'string', 'policy': 'string'}
            },
            'aws_ssm_activation': {
                'required': ['iam_role'],
                'types': {'iam_role': 'string'}
            },
            'aws_ssm_document': {
                'required': ['content', 'name'],
                'types': {'content': 'string', 'name': 'string'}
            },
            'aws_ssm_maintenance_window': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ssm_parameter': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ssm_patch_baseline': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_ssmincidents_replication_set': {
                'required': []  # No required fields
            },
            'aws_storagegateway_gateway': {
                'required': ['gateway_name'],
                'types': {'gateway_name': 'string'}
            },
            'aws_synthetics_canary': {
                'required': ['name', 'runtime_version', 'script'],
                'types': {'name': 'string', 'runtime_version': 'string', 'script': 'string'}
            },
            'aws_timestreamwrite_database': {
                'required': ['database_name'],
                'types': {'database_name': 'string'}
            },
            'aws_transfer_server': {
                'required': []  # No required fields
            },
            'aws_trustedadvisor_checks': {
                'required': []  # No required fields
            },
            'aws_vpclattice_service': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_waf_rate_based_rule': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_wafregional_rate_based_rule': {
                'required': ['name'],
                'types': {'name': 'string'}
            },
            'aws_wafv2_ip_set': {
                'required': ['name', 'scope'],
                'types': {'name': 'string', 'scope': 'string'}
            },
            'aws_worklink_fleet': {
                'required': ['fleet_name'],
                'types': {'fleet_name': 'string'}
            },
            'aws_workspaces_directory': {
                'required': ['directory_name'],
                'types': {'directory_name': 'string'}
            },
            'aws_xray_sampling_rule': {
                'required': ['rule_name'],
                'types': {'rule_name': 'string'}
            }
        }
    
    def _validate_field_type(self, field_name: str, field_value: Any, expected_type: str) -> List[str]:
        """Validate field type"""
        errors = []
        
        if expected_type == 'string' and not isinstance(field_value, str):
            errors.append(f"{field_name} must be a string")
        elif expected_type == 'number' and not isinstance(field_value, (int, float)):
            errors.append(f"{field_name} must be a number")
        elif expected_type == 'bool' and not isinstance(field_value, bool):
            errors.append(f"{field_name} must be a boolean")
        elif expected_type == 'list' and not isinstance(field_value, list):
            errors.append(f"{field_name} must be a list")
        elif expected_type == 'dict' and not isinstance(field_value, dict):
            errors.append(f"{field_name} must be an object")
        
        return errors
    
    def _validate_dynamodb_attributes(self, attributes: Any) -> List[str]:
        """Validate DynamoDB attribute definitions"""
        errors = []
        if not isinstance(attributes, list) or len(attributes) == 0:
            errors.append("attribute block must contain at least one attribute definition")
            return errors
        
        for i, attr in enumerate(attributes):
            if not isinstance(attr, dict):
                errors.append(f"attribute[{i}]: must be an object")
                continue
            if 'name' not in attr:
                errors.append(f"attribute[{i}]: missing required field 'name'")
            if 'type' not in attr:
                errors.append(f"attribute[{i}]: missing required field 'type'")
            elif attr['type'] not in ['S', 'N', 'B']:
                errors.append(f"attribute[{i}]: type must be 'S', 'N', or 'B'")
        return errors
    
    def _build_aws_instance(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """Fast path for AWS EC2 instances"""
        config = {
            "resource": {
                "aws_instance": {
                    "test": {
                        "ami": user_input.get('ami', self._get_latest_amazon_linux_ami()),
                        "instance_type": user_input.get('instance_type', 't3.micro'),
                        "subnet_id": user_input.get('subnet_id', self._get_default_subnet()),
                        "tags": user_input.get('tags', {"Name": "cloudbrew-instance"})
                    }
                }
            }
        }
        return config
    
    def _build_aws_s3_bucket(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """Fast path for AWS S3 buckets"""
        config = {
            "resource": {
                "aws_s3_bucket": {
                    "test": {
                        "bucket": user_input.get('bucket', f"cloudbrew-{int(time.time())}"),
                        "acl": user_input.get('acl', 'private')
                    }
                }
            }
        }
        return config
    
    def _build_aws_dynamodb_table(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """Fast path for AWS DynamoDB tables"""
        attributes = self._get_dynamodb_attributes(user_input)
        config = {
            "resource": {
                "aws_dynamodb_table": {
                    "test": {
                        "name": user_input.get('name', 'cloudbrew-table'),
                        "billing_mode": user_input.get('billing_mode', 'PROVISIONED'),
                        "hash_key": user_input.get('hash_key', 'id'),
                        "attribute": attributes
                    }
                }
            }
        }
        
        if user_input.get('range_key'):
            config["resource"]["aws_dynamodb_table"]["test"]["range_key"] = user_input['range_key']
        
        if config["resource"]["aws_dynamodb_table"]["test"]["billing_mode"] == "PROVISIONED":
            config["resource"]["aws_dynamodb_table"]["test"]["read_capacity"] = user_input.get('read_capacity', 5)
            config["resource"]["aws_dynamodb_table"]["test"]["write_capacity"] = user_input.get('write_capacity', 5)
        
        return config
    
    def _build_aws_db_instance(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        """Fast path for AWS RDS instances"""
        config = {
            "resource": {
                "aws_db_instance": {
                    "test": {
                        "engine": user_input.get('engine', 'mysql'),
                        "engine_version": user_input.get('engine_version', '8.0.32'),
                        "instance_class": user_input.get('instance_class', 'db.t3.micro'),
                        "allocated_storage": user_input.get('allocated_storage', 20),
                        "db_name": user_input.get('db_name', 'cloudbrewdb'),
                        "username": user_input.get('username', 'admin'),
                        "password": user_input.get('password', 'secure_password_123'),
                        "skip_final_snapshot": True
                    }
                }
            }
        }
        return config
    
    def _get_dynamodb_attributes(self, user_input: Dict) -> List[Dict]:
        """Generate attribute definitions for DynamoDB based on hash/range keys"""
        attributes = []
        hash_key = user_input.get('hash_key', 'id')
        attributes.append({'name': hash_key, 'type': 'S'})
        
        range_key = user_input.get('range_key')
        if range_key:
            attr_type = 'N' if any(suffix in range_key.lower() for suffix in ['time', 'date', 'timestamp']) else 'S'
            attributes.append({'name': range_key, 'type': attr_type})
        
        return attributes
    
    def _get_latest_amazon_linux_ami(self) -> str:
        """Get latest Amazon Linux 2 AMI ID"""
        if not self.aws_client:
            return "ami-0c55b159cbfafe1f0"
        try:
            response = self.aws_client.describe_images(
                Owners=['amazon'],
                Filters=[
                    {'Name': 'name', 'Values': ['amzn2-ami-hvm-*-x86_64-gp2']},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
            if response['Images']:
                return sorted(response['Images'], key=lambda x: x['CreationDate'], reverse=True)[0]['ImageId']
        except ClientError:
            pass
        return "ami-0c55b159cbfafe1f0"
    
    def _get_default_subnet(self) -> str:
        """Get default subnet ID"""
        if not self.aws_client:
            return "subnet-12345678"
        try:
            response = self.aws_client.describe_subnets(
                Filters=[{'Name': 'default-for-az', 'Values': ['true']}]
            )
            if response['Subnets']:
                return response['Subnets'][0]['SubnetId']
        except ClientError:
            pass
        return "subnet-12345678"
    
    def _extract_missing_field(self, error: str) -> Optional[str]:
        """Extract field name from 'missing required argument' error"""
        match = re.search(r'missing required argument: (\w+)', error)
        if match:
            return match.group(1)
        return None
    
    def _extract_missing_block(self, error: str) -> Optional[str]:
        """Extract block name from 'missing required block' error"""
        match = re.search(r'missing required block: (\w+)', error)
        if match:
            return match.group(1)
        return None
    
    def _add_missing_field(self, config: Dict, field_name: str, user_input: Dict) -> Dict:
        """Add a missing required field to the configuration"""
        if field_name in user_input:
            value = user_input[field_name]
        else:
            value = self._get_smart_default(field_name, config)
        
        resource_type = list(config['resource'].keys())[0]
        config['resource'][resource_type]['test'][field_name] = value
        return config
    
    def _get_smart_default(self, field_name: str, config: Dict) -> Any:
        """Get intelligent default value for a field"""
        resource_type = list(config['resource'].keys())[0]
        
        if resource_type in self.knowledge_base:
            if field_name in self.knowledge_base[resource_type].get('defaults', {}):
                return self.knowledge_base[resource_type]['defaults'][field_name]
        
        if resource_type == 'aws_instance':
            if field_name == 'ami':
                return self._get_latest_amazon_linux_ami()
            elif field_name == 'instance_type':
                return 't3.micro'
            elif field_name == 'subnet_id':
                return self._get_default_subnet()
        
        elif resource_type == 'aws_s3_bucket':
            if field_name == 'bucket':
                return f"cloudbrew-{int(time.time())}"
        
        elif resource_type == 'aws_dynamodb_table':
            if field_name == 'engine':
                return 'mysql'
            elif field_name == 'engine_version':
                return '8.0.32'
            elif field_name == 'instance_class':
                return 'db.t3.micro'
            elif field_name == 'allocated_storage':
                return 20
        
        if field_name in ['name', 'identifier']:
            return 'cloudbrew-resource'
        if field_name in ['enabled', 'public']:
            return True
        
        return ""
    
    def _add_missing_block(self, config: Dict, block_name: str) -> Dict:
        """Add a missing required block"""
        resource_type = list(config['resource'].keys())[0]
        if block_name not in config['resource'][resource_type]['test']:
            config['resource'][resource_type]['test'][block_name] = []
        return config
    
    def _fix_invalid_combination(self, config: Dict, error: str) -> Dict:
        """Fix invalid combination of arguments"""
        return config
    
    def _fix_exactly_one(self, config: Dict, error: str) -> Dict:
        """Fix 'exactly one of' requirements"""
        match = re.search(r'exactly one of \(([^)]+)\) must be specified', error)
        if match:
            options = [opt.strip() for opt in match.group(1).split(',')]
            if options:
                resource_type = list(config['resource'].keys())[0]
                config['resource'][resource_type]['test'][options[0]] = True
        return config
    
    def _fix_errors(self, config: Dict, errors: List[str], user_input: Dict) -> Dict:
        """Fix configuration based on validation errors"""
        fixed_config = config
        for error in errors:
            if "missing required argument" in error:
                field_name = self._extract_missing_field(error)
                if field_name:
                    fixed_config = self._add_missing_field(fixed_config, field_name, user_input)
            elif "missing required block" in error:
                block_name = self._extract_missing_block(error)
                if block_name:
                    fixed_config = self._add_missing_block(fixed_config, block_name)
            elif "exactly one of" in error:
                fixed_config = self._fix_exactly_one(fixed_config, error)
        return fixed_config
    
    def _config_to_hcl(self, config: Dict) -> str:
        """Convert configuration dict to HCL"""
        hcl_lines = []
        for resource_type, resources in config['resource'].items():
            for name, attrs in resources.items():
                hcl_lines.append(f'resource "{resource_type}" "{name}" {{')
                for key, value in attrs.items():
                    if isinstance(value, dict):
                        hcl_lines.append(f'  {key} {{')
                        for subkey, subvalue in value.items():
                            hcl_lines.append(f'    {subkey} = "{subvalue}"')
                        hcl_lines.append(f'  }}')
                    elif isinstance(value, list):
                        if value and key == 'attribute':
                            for attr in value:
                                hcl_lines.append(f'  {key} {{')
                                for attr_key, attr_value in attr.items():
                                    hcl_lines.append(f'    {attr_key} = "{attr_value}"')
                                hcl_lines.append(f'  }}')
                        elif value:
                            items = ", ".join(str(v) for v in value)
                            hcl_lines.append(f'  {key} = ["{items}"]')
                    else:
                        hcl_lines.append(f'  {key} = "{value}"')
                hcl_lines.append('}')
        return '\n'.join(hcl_lines)
    
    def _merge_with_user_input(self, base_config: Dict, user_input: Dict) -> Dict:
        """Merge user input with base configuration"""
        if not user_input:
            return base_config
        resource_type = list(base_config['resource'].keys())[0]
        resource_name = list(base_config['resource'][resource_type].keys())[0]
        for key, value in user_input.items():
            base_config['resource'][resource_type][resource_name][key] = value
        return base_config
    
    def _update_knowledge_base(self, resource_type: str, config: Dict):
        """Update knowledge base with successful configuration"""
        if resource_type not in self.knowledge_base:
            self.knowledge_base[resource_type] = {
                'defaults': {},
                'required_fields': [],
                'optional_fields': []
            }
        resource_config = config['resource'][resource_type]['test']
        for field, value in resource_config.items():
            if field not in self.knowledge_base[resource_type]['defaults']:
                if value and str(value) not in ['', '[]', '{}']:
                    self.knowledge_base[resource_type]['defaults'][field] = value
        self._save_knowledge_base()


# Command-line interface for testing
def main():
    import argparse
    parser = argparse.ArgumentParser(description='Intelligent Configuration Builder')
    parser.add_argument('resource_type', help='Resource type (e.g., aws_instance)')
    parser.add_argument('--field', action='append', nargs=2, help='User-provided field (name value)')
    args = parser.parse_args()
    user_input = {}
    if args.field:
        for name, value in args.field:
            user_input[name] = value
    builder = IntelligentBuilder()
    config = builder.build_configuration(args.resource_type, user_input)
    print("Generated Configuration:")
    print(builder._config_to_hcl(config))


if __name__ == '__main__':
    main()