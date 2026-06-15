pipeline {
    agent any

    triggers {
        pollSCM('H/5 * * * *')
    }

    environment {
        DOCKERHUB_USER = 'iamnmk777'
        IMAGE_NAME     = 'ecom-product-service'
        FULL_IMAGE     = "${DOCKERHUB_USER}/${IMAGE_NAME}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build & Push') {
            steps {
                script {
                    env.GIT_SHA = sh(returnStdout: true, script: 'git rev-parse --short HEAD').trim()
                }
                withCredentials([usernamePassword(credentialsId: 'dockerhub', usernameVariable: 'DH_USER', passwordVariable: 'DH_PASS')]) {
                    sh '''
                        echo "$DH_PASS" | docker login -u "$DH_USER" --password-stdin
                        docker build -t ${FULL_IMAGE}:latest -t ${FULL_IMAGE}:${GIT_SHA} .
                        docker push ${FULL_IMAGE}:latest
                        docker push ${FULL_IMAGE}:${GIT_SHA}
                    '''
                }
            }
        }
    }

    post {
        success {
            echo 'Image pushed. ArgoCD Image Updater will detect the new digest and sync to K3s.'
        }
    }
}
